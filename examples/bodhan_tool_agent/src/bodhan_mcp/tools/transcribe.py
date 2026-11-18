#!/usr/bin/env python3
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from ..client import client
from ..models import ErrorCode
from ..media import (
    inspect_audio_video,
    convert_to_wav_16khz_mono,
    chunk_wav_silence_aware,
    detect_script,
)
from ..tools._lang import normalize_lang


def _error(
    code: ErrorCode, message: str, retryable: bool = False,
    details: dict | None = None,
) -> dict:
    return {
        "error": code.value,
        "message": message,
        "code": code.value,
        "retryable": retryable,
        "details": details or {},
    }


async def transcribe_audio(
    file_path: str,
    language_hint: Optional[str] = None,
    max_duration_sec: Optional[float] = None,
) -> dict:
    path = Path(file_path)
    if not path.exists():
        return _error(
            ErrorCode.FILE_NOT_FOUND,
            f"File not found: {file_path}",
            retryable=False,
        )

    try:
        info = inspect_audio_video(str(path))
    except Exception as e:
        return _error(
            ErrorCode.MEDIA_PROCESSING_ERROR,
            f"Failed to inspect audio/video: {e}",
            retryable=False,
        )

    duration = info.get("duration_sec", 0)
    if duration <= 0:
        return _error(
            ErrorCode.VALIDATION_ERROR,
            f"Invalid audio duration: {duration}",
            retryable=False,
        )

    probe_mode = max_duration_sec is not None and max_duration_sec < duration
    wav_paths: list[tuple[str, tuple[float, float]]] = []

    try:
        if probe_mode:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                wav_path = tmp.name
            try:
                convert_to_wav_16khz_mono(str(path), wav_path)
            except Exception as e:
                Path(wav_path).unlink(missing_ok=True)
                return _error(
                    ErrorCode.MEDIA_PROCESSING_ERROR,
                    f"Failed to convert audio: {e}",
                    retryable=False,
                )
            wav_paths.append((wav_path, (0.0, max_duration_sec)))
        else:
            try:
                for idx, chunk_path, (start, end) in chunk_wav_silence_aware(str(path)):
                    wav_paths.append((chunk_path, (start, end)))
            except Exception as e:
                return _error(
                    ErrorCode.MEDIA_PROCESSING_ERROR,
                    f"Failed to chunk audio: {e}",
                    retryable=False,
                )

        segments = []
        full_text = ""
        total_cost = 0.0

        for wav_path, (start_sec, end_sec) in wav_paths:
            try:
                with open(wav_path, "rb") as f:
                    audio_data = f.read()
            except Exception as e:
                return _error(
                    ErrorCode.MEDIA_PROCESSING_ERROR,
                    f"Failed to read audio chunk: {e}",
                    retryable=False,
                )

            files = {"file": (Path(wav_path).name, audio_data, "audio/wav")}
            json_data = {"model": "indic-transcribe"}
            if language_hint:
                json_data["language"] = normalize_lang(language_hint)

            try:
                api_resp = await client.request(
                    "indic-transcribe",
                    "/audio/transcriptions",
                    json_data=json_data,
                    files=files,
                    timeout=60,
                )
                resp = api_resp.data
                cost = api_resp.cost_inr
            except Exception as e:
                return _error(
                    ErrorCode.NETWORK_ERROR,
                    f"Transcription API call failed: {e}",
                    retryable=True,
                )
            total_cost += cost

            text = resp.get("text", "").strip()
            if text:
                full_text += (" " + text) if full_text else text
                segments.append({
                    "start_sec": round(start_sec, 3),
                    "end_sec": round(end_sec, 3),
                    "text": text,
                })

        detected_script = detect_script(full_text)
        norm_hint = normalize_lang(language_hint) if language_hint else None
        return {
            "text": f"<untrusted_source_data>{full_text}</untrusted_source_data>",
            "language": norm_hint or detected_script,
            "detected_script": detected_script,
            "duration_sec": round(duration, 3),
            "cost_inr": round(total_cost, 4),
            "segments": segments,
            "cached": False,
        }

    finally:
        for wav_path, _ in wav_paths:
            try:
                Path(wav_path).unlink(missing_ok=True)
            except Exception:
                pass


__all__ = ["transcribe_audio"]
