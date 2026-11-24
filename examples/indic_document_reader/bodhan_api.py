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


def is_roman_script(text: str) -> bool:
    """Checks if text is predominantly Latin/Roman alphabet and not native Indic script."""
    if not text or not text.strip():
        return False
    latin_count = sum(1 for c in text if 'a' <= c.lower() <= 'z')
    indic_count = sum(1 for c in text if 0x0900 <= ord(c) <= 0x0D7F)
    if indic_count > 0 and indic_count >= latin_count:
        return False
    return latin_count > 0


_BRAHMIC_OFFSET_MAP = {
    # Vowels
    0x02: 'm', 0x03: 'h', 0x05: 'a', 0x06: 'aa', 0x07: 'i', 0x08: 'ee', 0x09: 'u', 0x0A: 'oo',
    0x0B: 'ri', 0x0E: 'e', 0x0F: 'ee', 0x10: 'ai', 0x12: 'o', 0x13: 'oo', 0x14: 'au',
    # Consonants
    0x15: 'k', 0x16: 'kh', 0x17: 'g', 0x18: 'gh', 0x19: 'ng',
    0x1A: 'ch', 0x1B: 'chh', 0x1C: 'j', 0x1D: 'jh', 0x1E: 'ny',
    0x1F: 't', 0x20: 'th', 0x21: 'd', 0x22: 'dh', 0x23: 'n',
    0x24: 't', 0x25: 'th', 0x26: 'd', 0x27: 'dh', 0x28: 'n',
    0x2A: 'p', 0x2B: 'ph', 0x2C: 'b', 0x2D: 'bh', 0x2E: 'm',
    0x2F: 'y', 0x30: 'r', 0x31: 'rr', 0x32: 'l', 0x33: 'll',
    0x35: 'v', 0x36: 'sh', 0x37: 'sh', 0x38: 's', 0x39: 'h',
    # Matras
    0x3E: 'aa', 0x3F: 'i', 0x40: 'ee', 0x41: 'u', 0x42: 'oo', 0x43: 'ri',
    0x46: 'e', 0x47: 'ee', 0x48: 'ai', 0x4A: 'o', 0x4B: 'oo', 0x4C: 'au',
    0x4D: ''  # Halant / Virama
}

_BRAHMIC_SCRIPT_BASES = [
    (0x0900, 0x097F),  # Devanagari (hi, mr, sa, ne, kok, mai, doi, sd)
    (0x0980, 0x09FF),  # Bengali / Assamese (bn, as, mni)
    (0x0A00, 0x0A7F),  # Gurmukhi (pa)
    (0x0A80, 0x0AFF),  # Gujarati (gu)
    (0x0B00, 0x0B7F),  # Odia (or)
    (0x0B80, 0x0BFF),  # Tamil (ta)
    (0x0C00, 0x0C7F),  # Telugu (te)
    (0x0C80, 0x0CFF),  # Kannada (kn)
    (0x0D00, 0x0D7F),  # Malayalam (ml)
]


def transliterate_indic_to_roman(text: str) -> str:
    """
    Converts Indic script text (Telugu, Hindi, Kannada, Tamil, etc.)
    into authentic phonetic Romanized Latin letters.
    """
    if not text:
        return ""
    result = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        code = ord(c)
        base = None
        for b_start, b_end in _BRAHMIC_SCRIPT_BASES:
            if b_start <= code <= b_end:
                base = b_start
                break
        if base is None:
            result.append(c)
            i += 1
            continue

        offset = code - base
        is_consonant = 0x15 <= offset <= 0x39
        roman_con = _BRAHMIC_OFFSET_MAP.get(offset, '')

        if is_consonant:
            if i + 1 < n:
                next_c = text[i + 1]
                next_code = ord(next_c)
                next_offset = next_code - base
                if next_offset == 0x4D:  # Virama
                    result.append(roman_con)
                    i += 2
                    continue
                elif 0x3E <= next_offset <= 0x4C:  # Matra vowel
                    result.append(roman_con + _BRAHMIC_OFFSET_MAP.get(next_offset, ''))
                    i += 2
                    continue
            result.append(roman_con + 'a')
            i += 1
        else:
            result.append(_BRAHMIC_OFFSET_MAP.get(offset, ''))
            i += 1

    return ''.join(result)


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
    # 1. Try dedicated /transliterate plain JSON endpoint
    body_plain = {
        "text": text,
        "language": language,
        "script": script,
    }
    if max_output_tokens:
        body_plain["max_output_tokens"] = max_output_tokens

    try:
        resp = requests.post(
            f"{BASE_URL}/transliterate",
            headers={**_auth_headers(api_key), "Content-Type": "application/json"},
            json=body_plain,
            timeout=30,
        )
        if resp.ok:
            data = resp.json()
            t_res = data.get("transliteration", "")
            if t_res and (script != "roman" or is_roman_script(t_res)):
                return TransliterateResponse(
                    transliteration=t_res,
                    language=language,
                    script=script,
                    truncated=data.get("truncated", False),
                    usage=data.get("usage", {}),
                )
    except Exception:
        pass

    # 2. Try /v1/chat/completions fallback
    try:
        body_chat: dict = {
            "model": "indic-transliterate",
            "messages": [{"role": "user", "content": text}],
            "language": language,
            "script": script,
        }
        if max_output_tokens:
            body_chat["max_tokens"] = max_output_tokens

        resp = requests.post(
            f"{BASE_URL}/v1/chat/completions",
            headers={**_auth_headers(api_key), "Content-Type": "application/json"},
            json=body_chat,
            timeout=30,
        )
        if resp.ok:
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                t_res = choices[0].get("message", {}).get("content", "")
                if t_res and (script != "roman" or is_roman_script(t_res)):
                    return TransliterateResponse(
                        transliteration=t_res,
                        language=language,
                        script=script,
                        truncated=choices[0].get("finish_reason") == "length",
                        usage=data.get("usage", {}),
                    )
    except Exception:
        pass

    # 3. Guaranteed phonetic transliteration for roman script
    if script == "roman":
        roman_fallback = transliterate_indic_to_roman(text)
        return TransliterateResponse(
            transliteration=roman_fallback,
            language=language,
            script=script,
            truncated=False,
            usage={},
        )

    return TransliterateResponse(
        transliteration=text,
        language=language,
        script=script,
        truncated=False,
        usage={},
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

