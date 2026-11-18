#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Optional

from ..client import client
from ..models import (
    SUPPORTED_TTS_LANGUAGES,
    TTS_VOICES,
    TTS_STYLES,
    ErrorCode,
)
from ..media import split_sentences, stitch_wav_ffmpeg


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


async def synthesize_speech(
    language: str,
    output_file: str,
    text: Optional[str] = None,
    input_file: Optional[str] = None,
    gender: str = "female",
    voice_id: Optional[str] = None,
    style: Optional[str] = None,
) -> dict:
    if language not in SUPPORTED_TTS_LANGUAGES:
        return _error(
            ErrorCode.UNSUPPORTED_LANGUAGE,
            f"Language '{language}' is not supported for speech synthesis",
            retryable=False,
            details={"supported": sorted(SUPPORTED_TTS_LANGUAGES)},
        )

    available_voices = TTS_VOICES.get(language, {})
    if voice_id:
        all_voices = [v for vs in available_voices.values() for v in vs]
        if voice_id not in all_voices:
            return _error(
                ErrorCode.UNSUPPORTED_VOICE,
                f"Voice '{voice_id}' is not available for language '{language}'",
                retryable=False,
                details={"available": all_voices},
            )
        voice = voice_id
    else:
        voice = available_voices.get(gender, ["Kavya"])[0]

    if style and style not in TTS_STYLES:
        supported = ", ".join(sorted(TTS_STYLES))
        return _error(
            ErrorCode.VALIDATION_ERROR,
            f"Style '{style}' is not supported. Supported: {supported}",
            retryable=False,
        )

    if input_file is None and text is None:
        return _error(
            ErrorCode.VALIDATION_ERROR,
            "Either text or input_file must be provided",
            retryable=False,
        )

    input_text = text
    if input_file:
        path = Path(input_file)
        if not path.exists():
            return _error(
                ErrorCode.FILE_NOT_FOUND,
                f"File not found: {input_file}",
                retryable=False,
            )
        if path.suffix.lower() in (".txt", ".md"):
            try:
                input_text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                return _error(
                    ErrorCode.MEDIA_PROCESSING_ERROR,
                    f"Failed to read input file: {e}",
                    retryable=False,
                )
        else:
            return _error(
                ErrorCode.VALIDATION_ERROR,
                f"Unsupported input file format: {path.suffix}",
                retryable=False,
                details={"supported": [".txt", ".md"]},
            )

    if not input_text or not input_text.strip():
        return _error(
            ErrorCode.VALIDATION_ERROR,
            "Input text is empty",
            retryable=False,
        )

    sentences = split_sentences(input_text, language)

    if not sentences:
        return _error(
            ErrorCode.VALIDATION_ERROR,
            "No valid sentences to synthesize",
            retryable=False,
        )

    instructions = {"lang": language}
    if style:
        instructions["style"] = style

    parts = []
    total_cost = 0.0

    for sent in sentences:
        try:
            api_resp = await client.request(
                "indic-speak",
                "/audio/speech",
                json_data={
                    "input": sent,
                    "voice": voice,
                    "instructions": json.dumps(instructions),
                    "response_format": "wav",
                },
                timeout=120,
            )
            resp = api_resp.data
            cost = api_resp.cost_inr
        except Exception as e:
            return _error(
                ErrorCode.NETWORK_ERROR,
                f"TTS API call failed: {e}",
                retryable=True,
            )
        total_cost += cost

        if isinstance(resp, dict) and "audio" in resp:
            import base64
            audio_bytes = base64.b64decode(resp["audio"])
        else:
            audio_bytes = resp

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_bytes)
            parts.append(tmp.name)

    if not parts:
        return _error(
            ErrorCode.MEDIA_PROCESSING_ERROR,
            "No audio segments generated",
            retryable=False,
        )

    try:
        stitch_wav_ffmpeg(parts, output_file)
    except Exception as e:
        for part in parts:
            try:
                Path(part).unlink(missing_ok=True)
            except Exception:
                pass
        return _error(
            ErrorCode.MEDIA_PROCESSING_ERROR,
            f"Failed to stitch audio: {e}",
            retryable=False,
        )

    for part in parts:
        try:
            Path(part).unlink(missing_ok=True)
        except Exception:
            pass

    import subprocess
    try:
        result = subprocess.run([
            "ffprobe", "-v", "quiet", "-show_entries",
            "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
            output_file
        ], capture_output=True, text=True, check=True)
        duration = float(result.stdout.strip())
    except Exception:
        duration = len(input_text) / 150.0

    return {
        "output_file": output_file,
        "duration_sec": round(duration, 3),
        "chunks_count": len(parts),
        "cost_inr": round(total_cost, 4),
        "language": language,
        "voice_id": voice,
        "gender": gender,
        "style": style,
        "cached": False,
    }


__all__ = ["synthesize_speech"]
