"""Speech checks.

Free signal checks (duration, silence, clipping, low-energy) use soundfile/numpy; ffmpeg is
used only to chunk long audio at silence boundaries. All audio libs are imported lazily and
degrade gracefully (a missing lib disables that signal, never crashes the run).

Paid ASR checks compare the back-transcription against the text that was spoken (the
translation) with our own CER/WER. ASR is a NOISY checker, so a high CER only becomes evidence
when it exceeds the per-language baseline + margin AND the edit operations are structured
(a contiguous omission, or a repetition) or a second signal agrees.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .models import Evidence
from .normalize import norm_for_asr
from .text_utils import char_error_rate


def transcript_is_degenerate(text: str) -> bool:
    """Reject pathological recognizer loops instead of attributing them to the audio."""
    return bool(re.search(r"([^\s])\1{15,}", text))


@dataclass
class SignalFeatures:
    duration_s: float
    leading_silence_s: float = 0.0
    trailing_silence_s: float = 0.0
    max_internal_silence_s: float = 0.0
    clipping_fraction: float = 0.0
    low_energy_fraction: float = 0.0
    exact_audio_repeat: tuple[float, float, float] | None = None
    rms_downsampled: list[float] = field(default_factory=list)  # for the dashboard waveform
    ok: bool = True
    error: str | None = None


def analyze_signal(audio_path: str, rms_buckets: int = 120) -> SignalFeatures:
    try:
        import numpy as np
        import soundfile as sf
    except Exception as exc:  # noqa: BLE001
        return SignalFeatures(duration_s=0.0, ok=False, error=f"audio libs unavailable: {exc}")
    try:
        data, sr = sf.read(audio_path, dtype="float32", always_2d=True)
    except Exception as exc:  # noqa: BLE001
        return SignalFeatures(duration_s=0.0, ok=False, error=f"read failed: {exc}")
    mono = data.mean(axis=1)
    n = len(mono)
    if n == 0 or sr == 0:
        return SignalFeatures(duration_s=0.0, ok=False, error="empty audio")
    duration = n / sr
    abs_mono = np.abs(mono)
    clip = float((abs_mono >= 0.999).mean())

    # frame energy for silence / low-energy analysis
    frame = max(1, int(0.02 * sr))  # 20 ms
    frame = min(frame, n)
    n_frames = max(1, n // frame)
    frames = mono[: n_frames * frame].reshape(n_frames, frame)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    peak = float(rms.max()) if rms.size else 0.0
    thresh = max(1e-4, peak * 0.1)
    silent = rms < thresh
    low_energy_fraction = float(silent.mean())

    lead = _run_len(silent, from_start=True) * frame / sr
    trail = _run_len(silent[::-1], from_start=True) * frame / sr
    max_internal = _max_internal_run(silent) * frame / sr

    # downsample rms for a compact waveform (dashboard); no audio bytes exported
    if rms.size >= rms_buckets:
        idx = (np.linspace(0, rms.size, rms_buckets + 1)).astype(int)
        buckets = [float(rms[idx[i]:idx[i + 1]].max()) if idx[i + 1] > idx[i] else 0.0
                   for i in range(rms_buckets)]
    else:
        buckets = [float(x) for x in rms]
    mx = max(buckets) or 1.0
    buckets = [round(b / mx, 3) for b in buckets]

    return SignalFeatures(
        duration_s=duration, leading_silence_s=lead, trailing_silence_s=trail,
        max_internal_silence_s=max_internal, clipping_fraction=clip,
        low_energy_fraction=low_energy_fraction, rms_downsampled=buckets,
        exact_audio_repeat=_exact_audio_repeat(mono, sr), ok=True,
    )


def _exact_audio_repeat(mono, rate: int) -> tuple[float, float, float] | None:
    """Find an exact repeated, non-silent half-second PCM segment in the first 30 seconds.

    This detects copied digital audio loops, not separately spoken repetitions. It is only
    corroborating evidence and must be combined with a separate transcription signal.
    """
    import numpy as np

    samples = np.asarray(mono[:rate * 30])
    window = rate // 2
    if window < 1 or len(samples) < window * 2:
        return None
    pcm = np.rint(np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    for start in range(0, len(samples) - window, window):
        segment = samples[start:start + window]
        if float(np.std(segment)) < 0.001:
            continue
        block = pcm[start * 2:(start + window) * 2]
        offset = pcm.find(block, (start + window) * 2)
        while offset >= 0 and offset % 2:
            offset = pcm.find(block, offset + 1)
        if offset >= 0:
            return (start / rate, offset / (2 * rate), window / rate)
    return None


def _run_len(mask, from_start: bool = True) -> int:
    count = 0
    for v in mask:
        if v:
            count += 1
        else:
            break
    return count


def _max_internal_run(mask) -> int:
    # longest run of True that is NOT touching either end
    best = cur = 0
    for i, v in enumerate(mask):
        if v:
            cur += 1
        else:
            if 0 < (i - cur):  # run ended and did not start at index 0
                best = max(best, cur)
            cur = 0
    return best


def signal_evidence(lang: str, spoken_text: str, feat: SignalFeatures, thresholds) -> list[Evidence]:
    if not feat.ok:
        return []
    out: list[Evidence] = []
    if feat.exact_audio_repeat:
        first, second, duration = feat.exact_audio_repeat
        out.append(Evidence(
            check="s4_audio_loop", category="speech_repetition", strength="medium", noisy=True,
            severity_hint="major", details={"first_start_s": round(first, 3),
                                            "repeat_start_s": round(second, 3),
                                            "matched_duration_s": round(duration, 3)},
        ))
    chars = max(1, len(norm_for_asr(spoken_text, lang)))
    cps = chars / feat.duration_s if feat.duration_s > 0 else 0.0
    high = thresholds.get("duration_cps_high", lang)
    if feat.duration_s > 0 and (cps > high):
        # too many characters per second -> audio likely too short (truncation/omission)
        out.append(Evidence(
            check="s1_duration", category="speech_omission", strength="medium", noisy=True,
            score=cps, severity_hint="major",
            details={"cps": round(cps, 2), "expected_max": high, "duration_s": round(feat.duration_s, 2)},
        ))
    sil = thresholds.get("silence_internal_seconds", lang)
    if feat.max_internal_silence_s >= sil:
        out.append(Evidence(
            check="s2_silence", category="speech_garbled", strength="weak", noisy=True,
            score=feat.max_internal_silence_s, severity_hint="minor",
            details={"max_internal_silence_s": round(feat.max_internal_silence_s, 2), "threshold": sil},
        ))
    clip_cut = thresholds.get("clipping_fraction", lang)
    if feat.clipping_fraction >= clip_cut:
        out.append(Evidence(
            check="s3_clipping", category="speech_garbled", strength="weak", noisy=True,
            score=feat.clipping_fraction, severity_hint="minor",
            details={"clipping_fraction": round(feat.clipping_fraction, 4), "threshold": clip_cut},
        ))
    return out


def asr_evidence(lang: str, spoken_text: str, asr_text: str, feat: SignalFeatures | None,
                 thresholds) -> list[Evidence]:
    """Compare ASR back-transcription against the spoken text. Only emit structured evidence."""
    ref = norm_for_asr(spoken_text, lang)
    hyp = norm_for_asr(asr_text, lang)
    if not ref:
        return []
    stats = char_error_rate(ref, hyp)
    cer = stats.rate
    baseline = thresholds.get("asr_cer_baseline", lang)
    margin = thresholds.get("asr_cer_margin", lang)
    out: list[Evidence] = []

    rep = _detect_repetition(ref, hyp)
    if rep is not None:
        out.append(Evidence(
            check="s5_repetition", category="speech_repetition", strength="medium", noisy=True,
            score=rep["count"], severity_hint="major",
            details={"repeated_ngram": rep["ngram"], "times": rep["count"], "cer": round(cer, 3)},
        ))

    # omission: large contiguous deletion at the tail (truncation) or middle
    deletions = [op for op in stats.ops if op[0] == "delete"]
    longest = _longest_deletion(stats.ops)
    minimum = max(3, math.ceil(len(ref) * thresholds.get("asr_min_deletion_fraction", lang)))
    if len(longest) >= minimum and cer > baseline + margin:
        tail = longest[-1] >= len(ref) - max(3, len(ref) // 10)
        out.append(Evidence(
            check="s6_omission", category="speech_omission", strength="medium", noisy=True,
            score=cer, severity_hint="major",
            details={"cer": round(cer, 3), "deleted_chars": len(deletions),
                     "longest_deletion": len(longest), "minimum_deletion": minimum,
                     "pattern": "truncation" if tail else "internal_omission"},
        ))

    # garbled: high CER with a corroborating signal (silence/clipping) or very short ASR
    sil_cut = thresholds.get("silence_internal_seconds", lang)
    clip_cut = thresholds.get("clipping_fraction", lang)
    signal_bad = bool(feat and (feat.max_internal_silence_s >= sil_cut
                                or feat.clipping_fraction >= clip_cut))
    too_short = len(hyp) < 0.5 * len(ref)
    if cer > baseline + margin and (signal_bad or too_short) and rep is None:
        out.append(Evidence(
            check="s7_garbled", category="speech_garbled", strength="medium", noisy=True,
            score=cer, severity_hint="major",
            details={"cer": round(cer, 3), "signal_bad": signal_bad, "asr_short": too_short},
        ))
    return out


def _longest_deletion(ops: list[tuple[str, int, int]]) -> list[int]:
    """Extract the longest contiguous source deletion, not scattered substitutions."""
    best: list[int] = []
    current: list[int] = []
    for operation, ref_index, _ in ops:
        if operation == "delete":
            current.append(ref_index)
            if len(current) > len(best):
                best = current.copy()
        else:
            current = []
    return best


def _detect_repetition(ref: str, hyp: str, n: int = 8) -> dict | None:
    """Find a character n-gram repeated in the ASR output but not repeated in the input."""
    if len(hyp) < 2 * n:
        return None
    from collections import Counter

    hyp_ngrams = Counter(hyp[i:i + n] for i in range(len(hyp) - n + 1))
    for ngram, _ in hyp_ngrams.most_common():
        if ngram.strip():
            # Both sides must use the same non-overlapping count. Comparing an overlapping
            # Counter with str.count() produced false repeats for naturally repetitive text.
            count = hyp.count(ngram)
            if count >= 2 and count > ref.count(ngram):
                return {"ngram": ngram, "count": count}
    return None
