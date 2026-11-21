from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
import tempfile
import wave
from typing import Any, Protocol

import httpx
import numpy as np

from .audio import FfmpegMissing, WavData, chunk_at_silence, to_wav
from .budget import Budget
from .client.errors import BodhanAPIError, BudgetExhausted
from .checks.speech import check_speech
from .dataset import DatasetResult
from .languages import get_lang
from .models import Flag, Item


class SpeechClient(Protocol):
    async def speak(self, text: str, voice: str, lang: str) -> bytes: ...

    async def transcribe(
        self,
        wav_bytes: bytes,
        language: str,
        *,
        duration_s: float | None = None,
    ) -> str: ...


def _round_robin_sample(items: list[Item], limit: int) -> list[Item]:
    groups: dict[str, list[Item]] = {}
    for item in sorted(items, key=lambda value: (value.lang, value.id)):
        groups.setdefault(item.lang, []).append(item)
    selected: list[Item] = []
    languages = sorted(groups)
    while len(selected) < limit and languages:
        remaining: list[str] = []
        for language in languages:
            group = groups[language]
            if group and len(selected) < limit:
                selected.append(group.pop(0))
            if group:
                remaining.append(language)
        languages = remaining
    return selected


def _wav_bytes(wav: WavData) -> bytes:
    samples = np.clip(wav.samples, -1.0, 1.0)
    pcm = (samples * 32767).astype("<i2").tobytes()
    buffer = BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(wav.sr)
        stream.writeframes(pcm)
    return buffer.getvalue()


def _serialize_flag(flag: Flag) -> dict[str, Any]:
    return {
        "category": flag.category,
        "severity": flag.severity,
        "modality": flag.modality,
        "span": list(flag.span) if flag.span is not None else None,
        "source_span": list(flag.source_span) if flag.source_span is not None else None,
        "audio_span_s": (
            list(flag.audio_span_s) if flag.audio_span_s is not None else None
        ),
        "evidence": flag.evidence,
        "detectors": flag.detectors,
        "confidence": flag.confidence,
        "suppressed_reason": flag.suppressed_reason,
    }


def _mark_failed(record: dict[str, Any], reason: str) -> None:
    record["terminal_state"] = "failed_checks"
    record["skipped_reason"] = None
    record["failed_checks"] = sorted(set(record.get("failed_checks", [])) | {reason})


def _error_code(error: Exception) -> str:
    if isinstance(error, BudgetExhausted):
        return "budget_exhausted"
    if isinstance(error, BodhanAPIError):
        return f"api_error:{error.status}"
    if isinstance(error, httpx.HTTPError):
        return "transport_error"
    if isinstance(error, FfmpegMissing):
        return "ffmpeg_missing"
    if isinstance(error, (OSError, wave.Error, EOFError)):
        return "invalid_audio"
    if isinstance(error, ValueError):
        return "invalid_audio"
    raise TypeError(f"unsupported speech-stage error: {type(error).__name__}")


def _is_unusable(record: dict[str, Any]) -> bool:
    return record.get("terminal_state") != "scored"


@contextmanager
def open_audio_bytes(audio: bytes):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "generated.wav"
        path.write_bytes(audio)
        yield path


def _append_speech_flags(record: dict[str, Any], flags: list[Flag]) -> None:
    record["flags"] = [
        *record.get("flags", []),
        *(_serialize_flag(flag) for flag in flags),
    ]


async def run_speech_stage(
    result: DatasetResult | list[Item],
    records: list[dict[str, Any]],
    client: SpeechClient | None,
    budget: Budget,
    *,
    enabled: bool,
    generate: bool,
    speech_sample: int,
    speak_available: bool = True,
    transcribe_available: bool = True,
) -> dict[tuple[str, str], str]:
    transcripts_by_item: dict[tuple[str, str], str] = {}
    items = result.items if isinstance(result, DatasetResult) else result
    record_map = {(record["id"], record["lang"]): record for record in records}
    audio_items = [
        item for item in items
        if item.audio is not None and item.translation and item.translation.strip()
    ]
    synthesis_sample = (
        _round_robin_sample(
            [
                item for item in items
                if item.audio is None
                and item.translation
                and item.translation.strip()
                and record_map.get((item.id, item.lang), {}).get("terminal_state") == "scored"
            ],
            speech_sample,
        )
        if generate
        else []
    )
    selected = {
        (item.id, item.lang): item
        for item in (*audio_items, *synthesis_sample)
    }
    if not enabled:
        for item in selected.values():
            record_map[(item.id, item.lang)]["speech"] = {"status": "disabled"}
        return transcripts_by_item

    for key in sorted(selected):
        item = selected[key]
        record = record_map[key]
        if _is_unusable(record):
            continue
        if not item.translation:
            continue
        generated = item.audio is None
        record["speech"] = {
            "status": "pending",
            "sampled": generated,
        }
        operation = "audio_load"
        try:
            if generated:
                if not speak_available:
                    record["speech"] = {
                        "status": "unavailable",
                        "sampled": True,
                        "reason": "speak_key_not_configured",
                    }
                    _mark_failed(record, "speech:speak_key_not_configured")
                    continue
                if client is None:
                    record["speech"] = {
                        "status": "unavailable",
                        "sampled": True,
                        "reason": "speech_client_not_configured",
                    }
                    _mark_failed(record, "speech:speech_client_not_configured")
                    continue
                cost = budget.estimate_speak(len(item.translation))
                if not budget.can_afford(cost):
                    budget.record_skip("speech_generation_budget_limit", cost)
                    record["speech"] = {
                        "status": "skipped",
                        "sampled": True,
                        "reason": "budget_limit",
                    }
                    _mark_failed(record, "speech:budget_limit")
                    continue
                language = get_lang(item.lang)
                if language is None:
                    raise ValueError("unknown_language")
                operation = "speak"
                audio_bytes = await client.speak(
                    item.translation,
                    language.default_voice,
                    item.lang,
                )
                with BytesIO(audio_bytes) as audio_stream:
                    with wave.open(audio_stream, "rb") as wav_file:
                        if (
                            wav_file.getnchannels() != 1
                            or wav_file.getsampwidth() != 2
                            or wav_file.getframerate() != 24_000
                        ):
                            raise ValueError("unexpected_speech_wav_format")
                with open_audio_bytes(audio_bytes) as audio_path:
                    wav = to_wav(audio_path)
            else:
                if item.audio is None:
                    continue
                wav = to_wav(item.audio)

            acoustic_flags = check_speech(item, wav)
            if not transcribe_available:
                _append_speech_flags(record, acoustic_flags)
                record["checks_run"] = sorted(
                    set(record.get("checks_run", [])) | {"speech_acoustic"}
                )
                record["speech"] = {
                    "status": "unavailable",
                    "sampled": generated,
                    "duration_s": round(wav.duration_s, 6),
                    "reason": "transcribe_key_not_configured",
                }
                _mark_failed(record, "speech:transcribe_key_not_configured")
                continue
            if client is None:
                _append_speech_flags(record, acoustic_flags)
                record["checks_run"] = sorted(
                    set(record.get("checks_run", [])) | {"speech_acoustic"}
                )
                record["speech"] = {
                    "status": "unavailable",
                    "sampled": generated,
                    "duration_s": round(wav.duration_s, 6),
                    "reason": "speech_client_not_configured",
                }
                _mark_failed(record, "speech:speech_client_not_configured")
                continue

            transcripts: list[str] = []
            chunks = chunk_at_silence(wav)
            try:
                operation = "transcribe"
                for chunk in chunks:
                    chunk_wav = WavData(samples=chunk.samples, sr=wav.sr)
                    transcripts.append(
                        await client.transcribe(
                            _wav_bytes(chunk_wav),
                            item.lang,
                            duration_s=chunk.end_s - chunk.start_s,
                        )
                    )
            except (BudgetExhausted, BodhanAPIError, httpx.HTTPError, ValueError) as error:
                _append_speech_flags(record, acoustic_flags)
                record["checks_run"] = sorted(
                    set(record.get("checks_run", [])) | {"speech_acoustic"}
                )
                reason = _error_code(error)
                if reason.startswith("api_error:") or reason == "transport_error":
                    reason = f"transcribe_{reason}"
                record["speech"] = {
                    "status": "unavailable",
                    "sampled": generated,
                    "duration_s": round(wav.duration_s, 6),
                    "chunks": len(chunks),
                    "reason": reason,
                }
                _mark_failed(record, f"speech:{reason}")
                continue

            transcript = " ".join(transcripts)
            transcripts_by_item[key] = transcript
            flags = check_speech(item, wav, transcript=transcript)
            _append_speech_flags(record, flags)
            record["checks_run"] = sorted(
                set(record.get("checks_run", [])) | {"speech_acoustic", "speech_asr"}
            )
            record["decisions"].append({
                "check": "speech",
                "reason": (
                    "Bodhan Indic-Speak sample synthesized and back-transcribed"
                    if generated
                    else "supplied audio acoustics and back-transcription checked"
                ),
            })
            record["speech"] = {
                "status": "complete",
                "sampled": generated,
                "duration_s": round(wav.duration_s, 6),
                "chunks": len(chunks),
                "transcript_characters": len(transcript),
            }
        except (
            BudgetExhausted,
            BodhanAPIError,
            httpx.HTTPError,
            FfmpegMissing,
            OSError,
            wave.Error,
            EOFError,
            ValueError,
        ) as error:
            reason = _error_code(error)
            if operation == "speak" and (
                reason.startswith("api_error:") or reason == "transport_error"
            ):
                reason = f"speak_{reason}"
            record["speech"] = {
                "status": "unavailable",
                "sampled": generated,
                "reason": reason,
            }
            _mark_failed(record, f"speech:{reason}")
    return transcripts_by_item
