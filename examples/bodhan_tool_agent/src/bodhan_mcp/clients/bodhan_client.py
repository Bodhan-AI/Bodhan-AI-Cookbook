"""
Async clients for each Bodhan model (OpenAI-compatible API).

Each call_* function returns (result, cost_inr) and handles retries internally.
Concurrency is limited to MAX_CONCURRENT simultaneous API calls across all models.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from ..tools.constants import SUPPORTED_LANGUAGES

# ── Pricing (INR) ────────────────────────────────────────────────────────────
COST_TRANSCRIBE_PER_MINUTE = 0.10
COST_TRANSLATE_PER_10K_TOKENS = 0.20
COST_SPEAK_PER_10K_CHARS = 6.0
COST_OCR_PER_IMAGE = 0.20

MAX_CONCURRENT = 2

_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    return _semaphore


def _get_base_url() -> str:
    return os.environ.get("BODHAN_API_BASE", "https://api.bodhan.ai/v1").rstrip("/")


def _should_retry(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False


async def _check_response(r: httpx.Response) -> None:
    if r.status_code == 429:
        retry_after_str = r.headers.get("Retry-After", "5")
        try:
            retry_after = float(retry_after_str)
        except ValueError:
            retry_after = 5.0
        await asyncio.sleep(max(retry_after, 5.0))
    r.raise_for_status()


_retry = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=3, max=45),
    retry=retry_if_exception(_should_retry),
    reraise=True,
)


@_retry
async def call_transcribe(audio_path: Path, language: str | None = None) -> tuple[dict, float]:
    """Transcribe audio chunk (≤ 30s) using Indic-Transcribe."""
    api_key = os.environ["BODHAN_TRANSCRIBE_KEY"]
    base_url = _get_base_url()
    
    # Calculate audio duration for metering
    from pydub import AudioSegment
    try:
        seg = AudioSegment.from_file(str(audio_path))
        duration_min = (len(seg) / 1000.0) / 60.0
    except Exception:
        duration_min = 0.5

    data = {"model": "indic-transcribe"}
    if language:
        data["language"] = language

    suffix = audio_path.suffix.lower().lstrip(".")
    mime = "audio/wav" if suffix == "wav" else f"audio/{suffix}"

    async with _get_semaphore():
        async with httpx.AsyncClient(timeout=120.0) as client:
            with open(audio_path, "rb") as f:
                r = await client.post(
                    f"{base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    data=data,
                    files={"file": (audio_path.name, f, mime)},
                )
                await _check_response(r)
                res = r.json()

    text = res.get("text", "")
    cost = duration_min * COST_TRANSCRIBE_PER_MINUTE
    return {
        "text": text,
        "language": language or "unknown",
        "segments": [{"start": 0.0, "end": round(duration_min * 60, 2), "text": text}],
    }, cost


@_retry
async def call_translate(text: str, source_lang: str, target_lang: str) -> tuple[str, float]:
    """Translate text chunk using Indic-Translate."""
    api_key = os.environ["BODHAN_TRANSLATE_KEY"]
    base_url = _get_base_url()

    src_name = SUPPORTED_LANGUAGES.get(source_lang.lower().strip(), source_lang)
    tgt_name = SUPPORTED_LANGUAGES.get(target_lang.lower().strip(), target_lang)

    async with _get_semaphore():
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "indic-translate",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                f"Translate the following text from {src_name} to {tgt_name}. "
                                f"Output only the {tgt_name} translation. "
                                "Preserve all Markdown, tables, LaTeX, "
                                "numbers, and formatting exactly."
                            ),
                        },
                        {"role": "user", "content": text},
                    ],
                },
            )
            await _check_response(r)
            res = r.json()

    translated = res["choices"][0]["message"]["content"].strip()
    usage = res.get("usage", {})
    output_tokens = usage.get("completion_tokens", len(translated) // 4)
    cost = (output_tokens / 10_000) * COST_TRANSLATE_PER_10K_TOKENS
    return translated, cost


@_retry
async def call_speak(text: str, language: str, voice: str) -> tuple[bytes, float]:
    """Synthesize speech chunk using Indic-Speak."""
    api_key = os.environ["BODHAN_SPEAK_KEY"]
    base_url = _get_base_url()

    instructions = json.dumps({"lang": language})

    async with _get_semaphore():
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{base_url}/audio/speech",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "indic-speak",
                    "input": text,
                    "voice": voice,
                    "instructions": instructions,
                },
            )
            await _check_response(r)
            audio_bytes = r.content

    cost = (len(text) / 10_000) * COST_SPEAK_PER_10K_CHARS
    return audio_bytes, cost


@_retry
async def call_ocr(image_path: Path) -> tuple[dict, float]:
    """OCR single image using Indic-OCR."""
    api_key = os.environ["BODHAN_OCR_KEY"]
    base_url = _get_base_url()

    suffix = image_path.suffix.lower().lstrip(".")
    mime = "image/png" if suffix == "png" else "image/jpeg"
    encoded = base64.b64encode(image_path.read_bytes()).decode()

    async with _get_semaphore():
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "indic-ocr",
                    "table_format": "markdown",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{mime};base64,{encoded}"},
                                }
                            ],
                        }
                    ],
                },
            )
            await _check_response(r)
            res = r.json()

    text = res["choices"][0]["message"]["content"].strip()
    return {"text": text, "confidence": 1.0}, COST_OCR_PER_IMAGE
