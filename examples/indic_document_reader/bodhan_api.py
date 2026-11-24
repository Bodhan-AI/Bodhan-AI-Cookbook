"""
Bodhan AI — Complete Python API Client
Covers all 5 endpoints: STT, Translation, Transliteration, TTS, Document OCR
"""

import requests
import base64
import tempfile
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


BASE_URL = "https://api.bodhan.ai"


# ── Exceptions ───────────────────────────────────────────────

class BodhanAPIError(Exception):
    """Raised when the Bodhan API returns an error."""

    def __init__(self, status: int, code: str, message: str, request_id: str = ""):
        self.status = status
        self.code = code
        self.request_id = request_id
        super().__init__(f"[{status}] {code}: {message}")


def _check_error(resp: requests.Response) -> None:
    """Raise BodhanAPIError if the response is not OK."""
    if resp.ok:
        return
    try:
        data = resp.json()
        err = data.get("error", data)
        raise BodhanAPIError(
            status=resp.status_code,
            code=err.get("code", "unknown_error"),
            message=err.get("message", f"HTTP {resp.status_code}"),
            request_id=err.get("request_id", ""),
        )
    except (ValueError, KeyError):
        raise BodhanAPIError(resp.status_code, "unknown_error", f"HTTP {resp.status_code}")


def _auth_headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


# ── 1. Speech to Text (STT) ─────────────────────────────────

@dataclass
class STTResponse:
    text: str


def speech_to_text(
    api_key: str,
    audio_bytes: bytes,
    language: Optional[str] = None,
    filename: str = "audio.wav",
) -> STTResponse:
    """
    Transcribe speech to text using indic-transcribe.
    Audio must be ≤30 seconds. WAV recommended; FLAC, OGG, MP3 also accepted.

    Args:
        api_key: Bodhan API key
        audio_bytes: Raw audio file bytes
        language: Optional language code (e.g., 'hi', 'te'). Strongly recommended.
        filename: Name for the uploaded file
    """
    files = {"file": (filename, audio_bytes, "audio/wav")}
    data = {"model": "indic-transcribe"}
    if language:
        data["language"] = language

    resp = requests.post(
        f"{BASE_URL}/v1/audio/transcriptions",
        headers=_auth_headers(api_key),
        files=files,
        data=data,
        timeout=60,
    )
    _check_error(resp)
    return STTResponse(text=resp.json()["text"])


# ── 2. Translation ──────────────────────────────────────────

@dataclass
class TranslateResponse:
    translation: str
    target_language: str
    script: str
    truncated: bool
    usage: dict = field(default_factory=dict)


def translate(
    api_key: str,
    text: str,
    target_language: str,
    script: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
) -> TranslateResponse:
    """
    Translate text into the target language.
    Supports 22 Indian languages + English. Uses /v1/chat/completions with model 'indic-translate'.

    Args:
        api_key: Bodhan API key
        text: Text to translate
        target_language: Target language code (e.g., 'kn', 'hi', 'te')
        script: 'native' (default), 'roman', or 'codemix'
        max_output_tokens: Optional token budget override
    """
    # Bodhan indic-translate on /v1/chat/completions
    body: dict = {
        "model": "indic-translate",
        "messages": [{"role": "user", "content": text}],
        "target_language": target_language,
    }
    if script:
        body["script"] = script
    if max_output_tokens:
        body["max_tokens"] = max_output_tokens

    resp = requests.post(
        f"{BASE_URL}/v1/chat/completions",
        headers={**_auth_headers(api_key), "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )
    _check_error(resp)
    data = resp.json()

    # Extract translation from chat completion choices
    translation = ""
    choices = data.get("choices", [])
    if choices:
        translation = choices[0].get("message", {}).get("content", "")

    return TranslateResponse(
        translation=translation,
        target_language=target_language,
        script=script or "native",
        truncated=choices[0].get("finish_reason") == "length" if choices else False,
        usage=data.get("usage", {}),
    )


# ── 3. Transliteration ──────────────────────────────────────

@dataclass
class TransliterateResponse:
    transliteration: str
    language: str
    script: str
    truncated: bool
    usage: dict = field(default_factory=dict)


def transliterate(
    api_key: str,
    text: str,
    language: str,
    script: str = "roman",
    max_output_tokens: Optional[int] = None,
) -> TransliterateResponse:
    """
    Rewrite text into a different script without translating.
    Default script is 'roman' (Latin letters). Uses model 'indic-transliterate'.

    Args:
        api_key: Bodhan API key
        text: Text to rewrite
        language: Language code (one of 22 Indic codes, not 'en')
        script: 'roman' (default) or 'native'
        max_output_tokens: Optional token budget override
    """
    body: dict = {
        "model": "indic-transliterate",
        "messages": [{"role": "user", "content": text}],
        "language": language,
        "script": script,
    }
    if max_output_tokens:
        body["max_tokens"] = max_output_tokens

    resp = requests.post(
        f"{BASE_URL}/v1/chat/completions",
        headers={**_auth_headers(api_key), "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )
    _check_error(resp)
    data = resp.json()

    transliteration = ""
    choices = data.get("choices", [])
    if choices:
        transliteration = choices[0].get("message", {}).get("content", "")

    return TransliterateResponse(
        transliteration=transliteration,
        language=language,
        script=script,
        truncated=choices[0].get("finish_reason") == "length" if choices else False,
        usage=data.get("usage", {}),
    )


# ── 4. Text to Speech (TTS) ─────────────────────────────────

def text_to_speech(
    api_key: str,
    text: str,
    voice: str,
    language: str,
    style: Optional[str] = None,
) -> bytes:
    """
    Convert text to speech. Returns WAV audio bytes (PCM16, 24 kHz, mono).
    Keep text to 1-2 sentences (~30s of speech).

    Args:
        api_key: Bodhan API key
        text: Text to read aloud
        voice: Voice persona name (e.g., 'Kavya', 'Amit', 'Deepika')
        language: Language code for the text
        style: Optional speaking style (e.g., 'news', 'children\\'s stories')
    """
    instructions = {"lang": language}
    if style:
        instructions["style"] = style

    import json
    body = {
        "model": "indic-speak",
        "input": text,
        "voice": voice,
        "instructions": json.dumps(instructions),
    }

    resp = requests.post(
        f"{BASE_URL}/v1/audio/speech",
        headers={**_auth_headers(api_key), "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )

    # TTS returns binary audio on success, JSON on error
    if not resp.ok:
        try:
            data = resp.json()
            err = data.get("error", data)
            raise BodhanAPIError(
                resp.status_code,
                err.get("code", "tts_error"),
                err.get("message", f"TTS failed: HTTP {resp.status_code}"),
            )
        except ValueError:
            raise BodhanAPIError(resp.status_code, "tts_error", f"TTS failed: HTTP {resp.status_code}")

    return resp.content


def chunk_text_for_tts(text: str, max_chars: int = 250) -> list:
    """
    Split long text at sentence boundaries (periods, question marks, danda '।', newlines)
    so each chunk is under ~250 chars (~15-25 seconds of speech), safely within
    Bodhan's 30s server limit.
    """
    import re
    clean = text.strip()
    if not clean:
        return []
    sentences = re.split(r'(?<=[.!?।\n])\s+', clean)
    chunks = []
    current = ""
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if len(current) + len(s) + 1 <= max_chars:
            current = f"{current} {s}" if current else s
        else:
            if current:
                chunks.append(current)
            # If a single sentence is longer than max_chars, split on punctuation or commas
            if len(s) > max_chars:
                sub_parts = re.split(r'(?<=[,;])\s+', s)
                sub_curr = ""
                for sp in sub_parts:
                    if len(sub_curr) + len(sp) + 1 <= max_chars:
                        sub_curr = f"{sub_curr} {sp}" if sub_curr else sp
                    else:
                        if sub_curr:
                            chunks.append(sub_curr)
                        sub_curr = sp
                if sub_curr:
                    chunks.append(sub_curr)
                current = ""
            else:
                current = s
    if current:
        chunks.append(current)
    return chunks


def text_to_speech_continuous(
    api_key: str,
    text: str,
    voice: str,
    language: str,
    style: Optional[str] = None,
    progress_callback = None,
) -> bytes:
    """
    Converts text of arbitrary length (e.g. full page, multiple paragraphs, or chapter)
    into a single continuous WAV audio stream.
    Splits into natural sentence chunks and concatenates WAV audio frames seamlessly,
    overcoming Bodhan's single-request 30s limit!
    """
    import io
    import wave

    chunks = chunk_text_for_tts(text, max_chars=250)
    if not chunks:
        return b""

    wav_segments = []
    for idx, chunk in enumerate(chunks):
        if progress_callback:
            progress_callback(idx + 1, len(chunks))
        seg_bytes = text_to_speech(
            api_key=api_key,
            text=chunk,
            voice=voice,
            language=language,
            style=style,
        )
        wav_segments.append(seg_bytes)

    if len(wav_segments) == 1:
        return wav_segments[0]

    # Stitch WAV files together by combining audio frames
    with wave.open(io.BytesIO(wav_segments[0]), "rb") as first_wav:
        params = first_wav.getparams()
        frames = [first_wav.readframes(first_wav.getnframes())]

    for seg in wav_segments[1:]:
        with wave.open(io.BytesIO(seg), "rb") as w:
            frames.append(w.readframes(w.getnframes()))

    out = io.BytesIO()
    with wave.open(out, "wb") as w_out:
        w_out.setparams(params)
        for f in frames:
            w_out.writeframes(f)

    return out.getvalue()


# ── 5. Document OCR ──────────────────────────────────────────

@dataclass
class OCRBlock:
    order: int
    label: str
    type: str
    bbox_xyxy: list
    conf: float
    text: str


@dataclass
class OCRResponse:
    id: str
    content: str  # Full page as reading-ordered Markdown
    blocks: list  # List of OCRBlock
    usage: dict = field(default_factory=dict)


def document_ocr(
    api_key: str,
    image_bytes: bytes,
    image_format: str = "png",
    table_format: str = "html",
    max_tokens: int = 2048,
) -> OCRResponse:
    """
    Extract text and layout from a document image using Indic-OCR.
    Supports PNG and JPEG only.

    Args:
        api_key: Bodhan API key
        image_bytes: Raw image bytes
        image_format: 'png' or 'jpeg'
        table_format: 'html' (default) or 'markdown'
        max_tokens: Per-block token limit (default 2048, raise for large tables)
    """
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    mime = "image/jpeg" if image_format.lower() in ("jpg", "jpeg") else "image/png"

    body: dict = {
        "model": "indic-ocr",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    }
                ],
            }
        ],
    }
    if table_format:
        body["table_format"] = table_format
    if max_tokens:
        body["max_tokens"] = max_tokens

    resp = requests.post(
        f"{BASE_URL}/v1/chat/completions",
        headers={**_auth_headers(api_key), "Content-Type": "application/json"},
        json=body,
        timeout=120,
    )
    _check_error(resp)
    data = resp.json()

    blocks = []
    for b in data.get("blocks", []):
        blocks.append(
            OCRBlock(
                order=b["order"],
                label=b["label"],
                type=b["type"],
                bbox_xyxy=b["bbox_xyxy"],
                conf=b["conf"],
                text=b["text"],
            )
        )

    return OCRResponse(
        id=data["id"],
        content=data["choices"][0]["message"]["content"],
        blocks=blocks,
        usage=data.get("usage", {}),
    )


# ── Pipeline Helpers ─────────────────────────────────────────

def scan_and_translate(
    ocr_key: str,
    translate_key: str,
    image_bytes: bytes,
    target_language: str,
    image_format: str = "png",
    script: Optional[str] = None,
    table_format: str = "markdown",
) -> dict:
    """
    OCR → Translate pipeline.
    Scans an image using ocr_key and translates using translate_key.
    """
    # Step 1: OCR
    ocr_result = document_ocr(
        api_key=ocr_key,
        image_bytes=image_bytes,
        image_format=image_format,
        table_format=table_format,
    )

    # Step 2: Translate each text block
    text_blocks = [b for b in ocr_result.blocks if b.text and b.text.strip()]
    translated_blocks = []

    for block in text_blocks:
        try:
            result = translate(
                api_key=translate_key,
                text=block.text,
                target_language=target_language,
                script=script,
            )
            translated_blocks.append({
                "block": block,
                "translation": result.translation,
                "truncated": result.truncated,
            })
        except BodhanAPIError as e:
            translated_blocks.append({
                "block": block,
                "translation": f"[Translation failed: {e}]",
                "truncated": False,
            })

    translated_text = "\n\n".join(tb["translation"] for tb in translated_blocks)

    return {
        "ocr_result": ocr_result,
        "translated_blocks": translated_blocks,
        "translated_text": translated_text,
    }


def listen_and_translate(
    stt_key: str,
    translate_key: str,
    audio_bytes: bytes,
    target_language: str,
    source_language: Optional[str] = None,
    script: Optional[str] = None,
) -> dict:
    """
    STT → Translate pipeline.
    Transcribes audio using stt_key and translates using translate_key.
    """
    # Step 1: STT
    stt_result = speech_to_text(
        api_key=stt_key,
        audio_bytes=audio_bytes,
        language=source_language,
    )

    # Step 2: Translate
    translate_result = translate(
        api_key=translate_key,
        text=stt_result.text,
        target_language=target_language,
        script=script,
    )

    return {
        "transcript": stt_result.text,
        "translation": translate_result.translation,
        "truncated": translate_result.truncated,
    }

