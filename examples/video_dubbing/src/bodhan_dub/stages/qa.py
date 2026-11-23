"""
Speech QA evaluation and bounded retry planner.
Back-transcribes synthesized audio with Indic-Transcribe, measures CER and acoustic defects,
and plans intelligent retries.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import scipy.signal
import soundfile as sf
from pydantic import BaseModel, Field

from bodhan_dub.gateway.bodhan import BodhanClient
from bodhan_dub.gateway.cost import BudgetExceededError
from bodhan_dub.gateway.resilience import CircuitBreakerOpen
from bodhan_dub.logging import PipelineLogger
from bodhan_dub.media.audio_proc import check_clipping, detect_internal_silence
from bodhan_dub.text.cer import compute_cer, detect_tail_omission
from bodhan_dub.text.normalize import detect_repeated_ngrams


class QAMetrics(BaseModel):
    cer: Optional[float] = None
    passed: bool
    verified: bool = True
    has_repetition: bool = False
    has_tail_omission: bool = False
    duration_ratio: float = 1.0  # actual / predicted
    is_silent: bool = False
    is_clipped: bool = False
    is_empty: bool = False
    failure_reasons: List[str] = Field(default_factory=list)


def determine_retry_action(
    failure_reasons: List[str],
    attempt: int = 1,
    previous_actions: Optional[List[str]] = None,
) -> str:
    """
    D1: Deterministic mapping from failure reasons to corrective action:
    - tail_omission_ or repetition_ -> split_text
    - duration_mismatch_ratio_ > 1.0 -> condense
    - cer_high_ -> paraphrase_source if attempt == 1 else condense
    - audio_clipping or internal_silence -> retry_same
    - default -> retry_same
    Never repeats an action in previous_actions.
    """
    prev = previous_actions or []
    candidate_action = "retry_same"

    if any(r.startswith("tail_omission") or r.startswith("repetition") for r in failure_reasons):
        candidate_action = "split_text"
    elif any(r.startswith("duration_mismatch_ratio_") for r in failure_reasons):
        ratio = 1.5
        for r in failure_reasons:
            if r.startswith("duration_mismatch_ratio_"):
                parts = r.split("_")
                try:
                    ratio = float(parts[-1].replace("advisory", ""))
                except Exception:
                    pass
        candidate_action = "condense" if ratio > 1.0 else "retry_same"
    elif any(r.startswith("cer_high_") for r in failure_reasons):
        candidate_action = "paraphrase_source" if attempt == 1 else "condense"
    elif any(r in ("audio_clipping", "internal_silence") for r in failure_reasons):
        candidate_action = "retry_same"

    # Avoid repeating strategies
    fallback_ladder = ["split_text", "paraphrase_source", "condense", "retry_same"]
    if candidate_action in prev:
        for fb in fallback_ladder:
            if fb not in prev:
                return fb
        return "retry_same"

    return candidate_action


@dataclass
class SynthesisCandidate:
    attempt: int
    text: str
    audio_48k: np.ndarray
    duration_s: float
    metrics: QAMetrics


def convert_48k_to_16k_wav_bytes(samples_48k: np.ndarray) -> bytes:
    """Downsamples 48kHz float32 audio to 16kHz mono WAV bytes for ASR evaluation."""
    if len(samples_48k) == 0:
        return b""
    # 48k -> 16k is 1/3 decimation (up=1, down=3)
    downsampled = scipy.signal.resample_poly(samples_48k, up=1, down=3)
    # Convert float32 to int16
    int16_samples = np.clip(downsampled * 32767.0, -32768.0, 32767.0).astype(np.int16)

    buf = io.BytesIO()
    with sf.SoundFile(
        buf, mode="w", format="WAV", subtype="PCM_16", samplerate=16000, channels=1
    ) as f:
        f.write(int16_samples)
    return buf.getvalue()


class SpeechQA:
    """Evaluates speech synthesis against reference text and plans retries."""

    def __init__(
        self,
        bodhan_client: Optional[BodhanClient],
        logger: PipelineLogger,
        cer_warn: float = 0.15,
        cer_fail: float = 0.30,
        cer_degraded_max: float = 0.45,
        max_latin_share: float = 0.30,
    ):
        self.bodhan_client = bodhan_client
        self.logger = logger
        self.cer_warn = cer_warn
        self.cer_fail = cer_fail
        self.cer_degraded_max = cer_degraded_max
        self.max_latin_share = max_latin_share

    async def evaluate(
        self,
        audio_48k: np.ndarray,
        reference_text: str,
        predicted_duration: float,
        lang: str,
        segment_id: str,
        is_warmed_up: bool = True,
    ) -> QAMetrics:
        """
        Back-transcribes synthesized audio and checks CER, repetitions, omissions, and clipping.
        """
        reasons: List[str] = []
        actual_dur = len(audio_48k) / 48000.0

        if actual_dur <= 0.05 or len(audio_48k) == 0:
            return QAMetrics(cer=1.0, passed=False, is_empty=True, failure_reasons=["empty_audio"])

        # Acoustic checks
        clipped = check_clipping(audio_48k)
        if clipped:
            reasons.append("audio_clipping")

        silent = detect_internal_silence(audio_48k, sample_rate=48000, min_silence_s=1.5)
        if silent:
            reasons.append("internal_silence")

        dur_ratio = actual_dur / max(0.1, predicted_duration)
        # Bounds per spec [0.60, 1.60]
        raw_mismatch = (dur_ratio < 0.60 or dur_ratio > 1.60)
        if raw_mismatch:
            if not is_warmed_up:
                # A6: Warmup segments treat duration-vs-prediction check as advisory (not a QA failure)
                reasons.append(f"duration_mismatch_ratio_{dur_ratio:.2f}_advisory")
                is_duration_mismatch = False
            else:
                reasons.append(f"duration_mismatch_ratio_{dur_ratio:.2f}")
                is_duration_mismatch = True
        else:
            is_duration_mismatch = False

        # Back-transcription with Indic-Transcribe
        wav_16k_bytes = convert_48k_to_16k_wav_bytes(audio_48k)
        back_transcription_failed = False
        try:
            resp = await self.bodhan_client.transcribe(
                audio_bytes=wav_16k_bytes,
                language=lang,
                response_format="json",
                duration_seconds=actual_dur,
                stage="qa",
                lang=lang,
                segment_id=segment_id,
            )
            hypothesis_text = resp.get("text", "").strip()
        except (BudgetExceededError, CircuitBreakerOpen):
            raise
        except Exception as e:
            self.logger.warning(
                f"Back-transcription failed during QA: {e}. Marking as degraded/unverified without burning TTS retries.",
                stage="qa",
                lang=lang,
                segment_id=segment_id,
            )
            hypothesis_text = ""
            back_transcription_failed = True
            reasons.append("back_transcription_failed_unverified")

        # Repetition detector (B14: reference-relative repetition check)
        has_repeat = False
        has_tail_omission = False
        if not back_transcription_failed:
            raw_has_repeat, repeated_str = detect_repeated_ngrams(
                hypothesis_text, min_n=1, max_n=3, repeat_threshold=3
            )
            # If the repetition also exists in reference_text (e.g. Indic reduplication like "बार-बार"), do NOT flag
            if raw_has_repeat and repeated_str and repeated_str in reference_text:
                has_repeat = False
            elif raw_has_repeat:
                has_repeat = True
                reasons.append(f"repetition_{repeated_str}")

            # Tail omission detector
            has_tail_omission, tail_str = detect_tail_omission(reference_text, hypothesis_text)
            if has_tail_omission:
                reasons.append(f"tail_omission_{tail_str}")

            # Compute CER
            cer = compute_cer(reference_text, hypothesis_text)
            if cer >= self.cer_fail:
                reasons.append(f"cer_high_{cer:.3f}")
            elif cer >= self.cer_warn:
                reasons.append(f"cer_warn_{cer:.3f}")

            passed = (
                (cer < self.cer_fail)
                and (not has_repeat)
                and (not has_tail_omission)
                and (not clipped)
                and (not silent)
                and (not is_duration_mismatch)
            )
            return QAMetrics(
                cer=round(cer, 4),
                passed=passed,
                verified=True,
                has_repetition=has_repeat,
                has_tail_omission=has_tail_omission,
                duration_ratio=round(dur_ratio, 2),
                is_silent=silent,
                is_clipped=clipped,
                is_empty=False,
                failure_reasons=reasons,
            )
        else:
            # D3: When back-transcription fails, cer=None, verified=False
            passed = (not clipped) and (not silent) and (not is_duration_mismatch)
            return QAMetrics(
                cer=None,
                passed=passed,
                verified=False,
                has_repetition=False,
                has_tail_omission=False,
                duration_ratio=round(dur_ratio, 2),
                is_silent=silent,
                is_clipped=clipped,
                is_empty=False,
                failure_reasons=reasons,
            )

    def plan_retry_action(
        self,
        metrics: QAMetrics,
        attempt: int = 1,
        previous_actions: Optional[List[str]] = None,
    ) -> str:
        """
        Chooses corrective action using deterministic unified logic:
        'split_text', 'paraphrase_source', 'condense', 'retry_same'.
        """
        reasons = list(metrics.failure_reasons)
        if metrics.has_tail_omission and not any(r.startswith("tail_omission") for r in reasons):
            reasons.append("tail_omission")
        if metrics.has_repetition and not any(r.startswith("repetition") for r in reasons):
            reasons.append("repetition")
        if metrics.cer is not None and metrics.cer >= self.cer_fail and not any(r.startswith("cer_high") for r in reasons):
            reasons.append(f"cer_high_{metrics.cer:.3f}")
        if (metrics.duration_ratio < 0.60 or metrics.duration_ratio > 1.20) and not any(r.startswith("duration_mismatch_ratio") for r in reasons):
            reasons.append(f"duration_mismatch_ratio_{metrics.duration_ratio:.2f}")
        if metrics.is_clipped and "audio_clipping" not in reasons:
            reasons.append("audio_clipping")
        if metrics.is_silent and "internal_silence" not in reasons:
            reasons.append("internal_silence")
        return determine_retry_action(reasons, attempt, previous_actions)
