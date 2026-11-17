#!/usr/bin/env python3
from __future__ import annotations
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp import TextResource
from typing import Optional
import json
import asyncio
from pathlib import Path
import tempfile
import subprocess

from models import (
    SUPPORTED_LANGUAGES,
    SUPPORTED_SPEECH_LANGUAGES,
    SPEECH_VOICES,
    CAPABILITIES,
    MediaInspectResult,
)
from media import (
    inspect_audio_video,
    detect_script,
    convert_to_wav_16khz_mono,
    stitch_wav,
    pdf_to_pngs,
    split_sentences,
)
from client import client

mcp = FastMCP(name="bodhan-mcp")


@mcp.resource("bodhan://capabilities")
async def get_capabilities() -> TextResource:
    return TextResource(
        uri="bodhan://capabilities",
        mimeType="application/json",
        text=json.dumps(CAPABILITIES, indent=2),
    )


@mcp.tool()
def inspect_media(file_path: str) -> MediaInspectResult:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    data = inspect_audio_video(str(path))
    script = detect_script(
        path.read_text(errors="ignore") if path.suffix in (".txt", ".md") else ""
    )
    lang = "unknown"
    if script and script != "latin":
        lang_map = {
            "devanagari": "hi", "tamil": "ta", "telugu": "te", "bengali": "bn",
            "kannada": "kn", "gujarati": "gu", "malayalam": "ml", "ori": "or", "hindi": "hi"
        }
        lang = lang_map.get(script, "unknown")

    min_cost = 0.0
    if path.suffix.lower() in (".wav", ".mp3", ".m4a", ".flac", ".ogg"):
        min_cost = data.get("duration_sec", 0) / 60 * 0.10

    return MediaInspectResult(
        format=path.suffix.lstrip(".") or "unknown",
        duration_sec=data.get("duration_sec", 0),
        bitrate=data.get("bitrate", 0),
        sample_rate=data.get("sample_rate"),
        channels=data.get("channels"),
        language=lang,
        min_cost_inr=min_cost,
    )


@mcp.tool()
def transcribe_audio(
    file_path: str,
    language_hint: Optional[str] = None,
    max_duration_sec: Optional[float] = None,
) -> str:
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

    files = {
        "file": (Path(path_str).name, audio_data, "audio/wav"),
    }
    json_data = {
        "model": "indic-transcribe",
    }
    if language_hint:
        json_data["language"] = language_hint

    result, cost = asyncio.run(client.request(
        "indic-transcribe",
        "/v1/audio/transcriptions",
        json_data=json_data,
        files=files,
        timeout=60,
    ))

    text = result.get("text", "")
    return f"<untrusted_source_data>{text}</untrusted_source_data>"


@mcp.tool()
def extract_document_text(file_path: str, page_range: Optional[str] = None) -> dict:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    result = {"markdown": "", "blocks": [], "pages": 0}

    if path.suffix.lower() == ".pdf":
        images = pdf_to_pngs(str(path))
    else:
        images = [str(path)]

    start, end = 0, len(images)
    if page_range:
        try:
            parts = page_range.split("-")
            start = int(parts[0]) - 1
            end = int(parts[-1]) if len(parts) > 1 else start + 1
        except Exception:
            pass

    import base64
    for i, img_path in enumerate(images[start:end]):
        with open(img_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        resp, cost = asyncio.run(client.request(
            "indic-ocr",
            "/v1/chat/completions",
            json_data={
                "model": "indic-ocr",
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
                ]}],
                "table_format": "markdown",
            },
            timeout=120,
        ))
        choice = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
        blocks = resp.get("blocks", [])
        result["markdown"] += choice + "\n"
        result["blocks"].extend(blocks)

    result["pages"] = len(result["blocks"])
    return result


@mcp.tool()
def translate_text(
    source_lang: str,
    target_lang: str,
    text: Optional[str] = None,
    input_file: Optional[str] = None,
    output_file: Optional[str] = None,
) -> dict:
    if target_lang not in SUPPORTED_LANGUAGES:
        return {
            "error": "UNSUPPORTED_LANGUAGE",
            "message": (
                f"Language '{target_lang}' is not supported. "
                f"Supported languages: {', '.join(sorted(SUPPORTED_LANGUAGES))}"
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
        if path.suffix.lower() in (".txt", ".md"):
            input_text = path.read_text(encoding="utf-8", errors="ignore")

    resp, cost = asyncio.run(client.request(
        "indic-translate",
        "/translate",
        json_data={
            "text": input_text,
            "target_language": target_lang,
            "source_language": source_lang
        },
        timeout=60,
    ))

    result = {
        "translation": f"<untrusted_source_data>{resp.get('translation', '')}</untrusted_source_data>",
        "target_language": target_lang,
        "script": resp.get("script", "native"),
        "truncated": resp.get("truncated", False),
        "cost_inr": cost,
    }
    return result


@mcp.tool()
def synthesize_speech(
    language: str,
    output_file: str,
    text: Optional[str] = None,
    input_file: Optional[str] = None,
    gender: str = "female",
    voice_id: Optional[str] = None,
) -> dict:
    if language not in SUPPORTED_SPEECH_LANGUAGES:
        return {
            "error": "UNSUPPORTED_LANGUAGE",
            "message": (
                f"Language '{language}' is not supported for speech synthesis. "
                f"Supported languages: {', '.join(sorted(SUPPORTED_SPEECH_LANGUAGES))}"
            ),
            "code": "unsupported_language",
        }

    if input_file:
        path = Path(input_file)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {input_file}")
        text = path.read_text(encoding="utf-8", errors="ignore")

    sentences = split_sentences(text or "", language)
    voice = voice_id or SPEECH_VOICES.get(language, {}).get(gender, ["Kavya"])[0]

    parts = []
    total_cost = 0.0
    for sent in sentences:
        if len(sent) < 10:
            continue
        temp_out = tempfile.mktemp(suffix=".wav")
        resp, cost = asyncio.run(client.request(
            "indic-speak",
            "/v1/audio/speech",
            json_data={
                "model": "indic-speak",
                "input": sent,
                "voice": voice,
                "instructions": json.dumps({"lang": language}),
                "response_format": "wav",
            },
            timeout=120,
        ))
        if isinstance(resp, dict) and "audio" in resp:
            import base64
            audio_bytes = base64.b64decode(resp["audio"])
        else:
            audio_bytes = resp
        with open(temp_out, "wb") as f:
            f.write(audio_bytes)
        parts.append(temp_out)
        total_cost += cost

    final_path = stitch_wav(parts, output_file)

    return {
        "path": final_path,
        "gender": gender,
        "language": language,
        "voice_id": voice,
        "duration_sec": len(text or "") / 150,
        "cost_inr": total_cost,
    }