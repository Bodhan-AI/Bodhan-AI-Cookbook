#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
from typing import Optional
from ..client import client
from ..media import convert_to_wav_16khz_mono


async def transcribe(
    file_path: str,
    language_hint: Optional[str] = None,
    max_duration_sec: Optional[float] = None,
) -> dict:
    import tempfile
    import subprocess
    import wave

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    wav_path = None
    if max_duration_sec:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = tmp.name
        subprocess.run([
            "ffmpeg", "-y", "-i", str(path),
            "-t", str(max_duration_sec),
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav_path
        ], capture_output=True, check=True)
        path_str = wav_path
    else:
        wav_path = convert_to_wav_16khz_mono(str(path), str(path) + ".converted.wav")
        path_str = wav_path

    with open(path_str, "rb") as f:
        audio_data = f.read()

    duration = 0
    if path_str.endswith(".wav"):
        with wave.open(path_str, "rb") as wf:
            duration = wf.getnframes() / wf.getframerate()

    files = {
        "file": (Path(path_str).name, audio_data, "audio/wav"),
    }
    json_data = {
        "model": "indic-transcribe",
    }
    if language_hint:
        json_data["language"] = language_hint

    resp, cost = await client.request(
        "indic-transcribe",
        "/v1/audio/transcriptions",
        json_data=json_data,
        files=files,
        timeout=60,
    )

    text = resp.get("text", "")
    return {
        "text": f"<untrusted_source_data>{text}</untrusted_source_data>",
        "language": language_hint or "auto",
        "duration_sec": duration,
        "cost_inr": cost,
        "segments": [{"start": 0, "end": duration, "text": text}],
    }


async def identify_by_script(file_path: str, target_script: str) -> list[dict]:
    import re

    result = await transcribe(file_path)
    text = result.get("text", "").replace(
        "<untrusted_source_data>", ""
    ).replace("</untrusted_source_data>", "")

    matching_lines = []
    for line in text.split("
"):
        if re.search(r"[\u0C00-\u0C7F]", line):
            matching_lines.append({"text": line, "language": "telugu"})

    return matching_lines


__all__ = ["transcribe", "identify_by_script"]
