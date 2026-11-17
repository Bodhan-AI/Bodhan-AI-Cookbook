#!/usr/bin/env python3
from __future__ import annotations
import json
import tempfile
from pathlib import Path
from typing import Optional
from ..client import client
from ..media import split_sentences, stitch_wav

async def synthesize_speech(
    language: str,
    text: Optional[str] = None,
    input_file: Optional[str] = None,
    output_file: Optional[str] = None,
    gender: str = "female",
    voice_id: Optional[str] = None,
) -> dict:
    from ..models import SUPPORTED_SPEECH_LANGUAGES, SPEECH_VOICES

    if language not in SUPPORTED_SPEECH_LANGUAGES:
        return {
            "error": "UNSUPPORTED_LANGUAGE",
            "message": (
                f"Language '{language}' is not supported for speech synthesis. "
                f"Supported languages: {', '.join(sorted(SUPPORTED_SPEECH_LANGUAGES))}"
            ),
            "code": "unsupported_language",
        }

    if input_file is None and text is None:
        raise ValueError("Either text or input_file must be provided")

    input_text = text
    if input_file:
        path = Path(input_file)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {input_file}")
        input_text = path.read_text(encoding="utf-8", errors="ignore")

    sentences = split_sentences(input_text, language)
    voice = voice_id or SPEECH_VOICES.get(language, {}).get(gender, ["Kavya"])[0]

    parts = []
    total_cost = 0.0
    for sent in sentences:
        if len(sent) < 10:
            continue
        temp_out = tempfile.mktemp(suffix=".wav")
        resp, cost = await client.request(
            "indic-speak",
            "/v1/audio/speech",
            json_data={
                "model": "indic-speak",
                "input": sent,
                "voice": voice,
                "instructions": json.dumps({"lang": language}),
                "response_format": "wav",
            },
        )
        if isinstance(resp, dict) and "audio" in resp:
            import base64
            audio_bytes = base64.b64decode(resp["audio"])
        else:
            audio_bytes = resp
        with open(temp_out, "wb") as f:
            f.write(audio_bytes)
        parts.append(temp_out)
        total_cost += cost

    if output_file:
        stitch_wav(parts, output_file)

    return {
        "path": output_file or "output.wav",
        "gender": gender,
        "language": language,
        "voice_id": voice,
        "cost_inr": total_cost,
        "duration_sec": len(input_text) / 150,
    }

__all__ = ["synthesize_speech"]
