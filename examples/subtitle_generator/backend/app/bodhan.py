"""Calls to the Bodhan API: indic-transcribe (speech to text) and indic-translate (translation).

With DEV_MOCK_MODE=true both return canned text, so the app runs without a key.
"""

import asyncio
import logging
import re

import httpx

from .config import settings

logger = logging.getLogger(__name__)

# Model ids from the cookbook's scripts/bodhan_api_rules.json
TRANSCRIBE_MODEL = "indic-transcribe"
TRANSLATE_MODEL = "indic-translate"

# Waits between retries after HTTP 429, when the response doesn't say how long to wait
BACKOFF_SECONDS = [3.0, 6.0, 12.0, 20.0, 25.0]

MOCK_TRANSCRIPTS = {
    "hi": ["नमस्ते, बोधन स्टूडियो में आपका स्वागत है।", "यह वीडियो सबटाइटल जनरेटर का परीक्षण कर रहा है।"],
    "en": ["Hello and welcome to Bodhan Studio.", "This video tests the subtitle generator."],
}


class BodhanError(Exception):
    """A Bodhan call failed. `status` is the HTTP status the app should answer with."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _headers(key: str, name: str) -> dict[str, str]:
    if not key:
        raise BodhanError(f"No Bodhan API key. Set {name} in .env, or DEV_MOCK_MODE=true to try the app without one.", 503)
    return {"Authorization": f"Bearer {key}"}


def check_keys(translate: bool) -> None:
    """Raise BodhanError (503) now if a needed key is missing, rather than partway through a job."""
    if not settings.dev_mock_mode:
        _headers(settings.transcribe_key, "BODHAN_TRANSCRIBE_API_KEY")
        if translate:
            _headers(settings.translate_key, "BODHAN_TRANSLATE_API_KEY")


def _retry_wait(response: httpx.Response, attempt: int) -> float:
    """Seconds to wait after a 429: the Retry-After header, else the gateway's "try again in Ns", else backoff."""
    if response.headers.get("Retry-After", "").isdigit():
        return float(response.headers["Retry-After"])
    try:
        message = str(response.json().get("error", {}).get("message", ""))
    except Exception:
        message = ""
    matched = re.search(r"in\s+(\d+(?:\.\d+)?)\s*s", message)
    if matched:
        return float(matched.group(1)) + 1.0
    return BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]


async def _send(service: str, request) -> httpx.Response:
    """Await request() for a response, retrying on HTTP 429; raise BodhanError on any other failure."""
    for attempt in range(len(BACKOFF_SECONDS)):
        try:
            response = await request()
        except httpx.RequestError as exc:
            raise BodhanError(f"Could not reach Bodhan {service}: {exc}") from None
        if response.status_code != 429:
            if response.is_error:
                raise BodhanError(f"Bodhan {service} returned HTTP {response.status_code}: {response.text[:200]}")
            return response
        if attempt < len(BACKOFF_SECONDS) - 1:
            wait = _retry_wait(response, attempt)
            logger.warning("Bodhan %s rate limited (429). Retrying in %.1fs.", service, wait)
            await asyncio.sleep(wait)
    raise BodhanError(f"Bodhan {service} is rate limiting requests. Wait a minute and try again.", 429)


class Transcript:
    """What indic-transcribe returned for one piece of audio. Times in `words`/`segments` are relative to the piece."""

    def __init__(self, text: str, words: list[dict] | None = None, segments: list[dict] | None = None):
        self.text = text
        self.words = words or None  # [{"word", "start", "end"}]
        self.segments = segments or None  # [{"text", "start", "end"}]


async def transcribe(client: httpx.AsyncClient, wav_path: str, language: str, piece_index: int = 0) -> Transcript:
    """Send one WAV piece (up to ~30 s) to indic-transcribe."""
    if settings.dev_mock_mode:
        sentences = MOCK_TRANSCRIPTS.get(language, MOCK_TRANSCRIPTS["hi"])
        return Transcript(sentences[piece_index % len(sentences)])

    headers = _headers(settings.transcribe_key, "BODHAN_TRANSCRIBE_API_KEY")
    form = {"model": TRANSCRIBE_MODEL, "language": language}
    if settings.bodhan_timestamps:
        # OpenAI-style request for timings. Accepted today, but `words`/`segments` come back empty
        # (Oct 2026); when Bodhan fills them, subtitles use the real times instead of estimates.
        # Don't add timestamp_granularities: the API currently rejects it with 422.
        form["response_format"] = "verbose_json"

    async def request():
        with open(wav_path, "rb") as audio:
            return await client.post(
                f"{settings.bodhan_base_url}/v1/audio/transcriptions",
                headers=headers,
                data=form,
                files={"file": ("audio.wav", audio, "audio/wav")},
            )

    data = (await _send("indic-transcribe", request)).json()
    if not isinstance(data.get("text"), str):
        raise BodhanError("indic-transcribe returned no text.")
    return Transcript(data["text"], words=_timed(data.get("words"), "word"), segments=_timed(data.get("segments"), "text"))


def _timed(items, text_key: str) -> list[dict] | None:
    """Keep only well-formed timed items ({text_key, start, end} with numbers); None if there are none."""
    if not isinstance(items, list):
        return None
    valid = [
        {text_key: item[text_key], "start": float(item["start"]), "end": float(item["end"])}
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get(text_key), str)
        and isinstance(item.get("start"), (int, float))
        and isinstance(item.get("end"), (int, float))
    ]
    return valid or None


# /translate is the documented route but returned 404 on the live API (Oct 2026). After the first 404
# we use the documented equivalent, /v1/chat/completions, for the rest of the run.
_translate_route_missing = False


async def translate(client: httpx.AsyncClient, text: str, target_language: str) -> str:
    """Translate text into target_language with indic-translate (the source language is detected)."""
    global _translate_route_missing
    if settings.dev_mock_mode:
        return f"[{target_language}] {text}"

    headers = _headers(settings.translate_key, "BODHAN_TRANSLATE_API_KEY")

    async def request():
        global _translate_route_missing
        if not _translate_route_missing:
            response = await client.post(
                f"{settings.bodhan_base_url}/translate",
                headers=headers,
                json={"text": text, "target_language": target_language, "script": "native"},
            )
            if response.status_code != 404:
                return response
            _translate_route_missing = True
            logger.info("/translate returned 404; using /v1/chat/completions for translation.")
        return await client.post(
            f"{settings.bodhan_base_url}/v1/chat/completions",
            headers=headers,
            json={"model": TRANSLATE_MODEL, "messages": [{"role": "user", "content": text}], "target_language_code": target_language},
        )

    data = (await _send("indic-translate", request)).json()
    if "choices" in data:  # chat-completions response
        choice = data["choices"][0]
        translation, truncated = choice["message"].get("content"), choice.get("finish_reason") == "length"
    else:
        translation, truncated = data.get("translation"), bool(data.get("truncated"))
    if truncated:
        logger.warning("indic-translate cut off its output (input was %d characters).", len(text))  # no user text in logs
    if not isinstance(translation, str) or not translation.strip():
        raise BodhanError("indic-translate returned no text.")
    return translation.strip()
