"""The real connection to Bodhan: translate() and speak().

Both calls go through one helper that adds the key, retries politely and turns
Bodhan's errors into messages a person can understand. Pasted text, translations
and keys are never written to the log.
"""

import json
import logging
import time

import requests

from . import config
from .errors import BodhanAPIError

logger = logging.getLogger(__name__)

TIMEOUT = (10, 60)  # seconds to connect, seconds to wait for the answer
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (1, 2, 4)
MAX_WAIT_SECONDS = 30

MESSAGES_BY_CODE = {
    "invalid_api_key_error": "Bodhan did not accept the API key. Check the key in your .env file.",
    "rate_limit_exceeded_error": (
        "Bodhan is limiting requests right now, or your credit has run out. "
        "Wait a moment and try again, and check your balance in the console."
    ),
    "payload_too_large": "That paragraph is too long for Bodhan. Split it into shorter paragraphs.",
    "unsupported_language": "Bodhan does not support that language.",
    "invalid_request_error": "Bodhan could not understand the request.",
    "internal_error": "Bodhan had a problem on its side. Try again in a moment.",
}
MESSAGES_BY_STATUS = {
    401: MESSAGES_BY_CODE["invalid_api_key_error"],
    413: MESSAGES_BY_CODE["payload_too_large"],
    422: "Bodhan could not read that text aloud.",
    429: MESSAGES_BY_CODE["rate_limit_exceeded_error"],
}


def _request_id(response):
    """Bodhan sends the id under different names depending on the route."""
    for name in ("x-request-id", "llm_provider-x-request-id"):
        value = response.headers.get(name)
        if value:
            return value
    return None


def _to_error(response):
    """Build a BodhanAPIError from a failed response."""
    status = response.status_code
    request_id = _request_id(response)
    code = None
    detail = None
    try:
        data = response.json()
    except ValueError:
        data = None
        detail = (response.text or "")[:500] or None
    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        problem = data["error"]
        code = problem.get("code")
        detail = problem.get("message")
        request_id = problem.get("request_id") or request_id
    elif isinstance(data, dict):
        detail = str(data.get("detail") or data)[:500]
    if code in MESSAGES_BY_CODE:
        message = MESSAGES_BY_CODE[code]
    elif status in MESSAGES_BY_STATUS:
        message = MESSAGES_BY_STATUS[status]
    elif status >= 500:
        message = MESSAGES_BY_CODE["internal_error"]
    else:
        message = f"Bodhan returned an error (status {status})."
    return BodhanAPIError(
        message, status=status, code=code, request_id=request_id, detail=detail
    )


def _wait_seconds(response, attempt):
    """How long to wait before trying again: Bodhan's retry-after, else our schedule."""
    retry_after = response.headers.get("retry-after")
    if response.status_code == 429 and retry_after:
        try:
            return min(float(retry_after), MAX_WAIT_SECONDS)
        except ValueError:
            pass
    return BACKOFF_SECONDS[attempt - 1]


class BodhanClient:
    def __init__(self, session=None, sleep=time.sleep):
        self.session = session or requests.Session()
        self.sleep = sleep
        self._worked = set()  # which calls (translate/speak) have succeeded before

    def _post(self, kind, path, **kwargs):
        """POST to Bodhan, retrying on 429, 5xx and network trouble. Returns the response."""
        key = config.get_api_key(kind)
        url = config.BASE_URL + path
        headers = {"Authorization": f"Bearer {key}"}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            started = time.monotonic()
            try:
                response = self.session.post(url, headers=headers, timeout=TIMEOUT, **kwargs)
            except (requests.ConnectionError, requests.Timeout) as problem:
                logger.info("bodhan %s network problem, attempt %d", kind, attempt)
                if attempt == MAX_ATTEMPTS:
                    raise BodhanAPIError(
                        "Could not reach Bodhan. Check your internet connection and try again.",
                        code="connection_error",
                    ) from problem
                self.sleep(BACKOFF_SECONDS[attempt - 1])
                continue
            logger.info(
                "bodhan %s status=%s ms=%d request_id=%s attempt=%d",
                kind,
                response.status_code,
                (time.monotonic() - started) * 1000,
                _request_id(response),
                attempt,
            )
            if response.status_code == 200:
                self._worked.add(kind)
                return response
            status = response.status_code
            # Bodhan says an auth error is worth retrying if this key worked recently.
            retryable = status == 429 or status >= 500 or (status == 401 and kind in self._worked)
            if retryable and attempt < MAX_ATTEMPTS:
                self.sleep(_wait_seconds(response, attempt))
                continue
            raise _to_error(response)

    def translate(self, text, target_language):
        """Translate text. Returns (translation, truncated).

        Uses the chat-completions route with the indic-translate model. A reply that
        did not finish normally (finish_reason is not "stop") counts as truncated, so
        an incomplete translation is never read aloud.
        """
        response = self._post(
            "translate",
            "/v1/chat/completions",
            json={
                "model": "indic-translate",
                "messages": [{"role": "user", "content": text}],
                "target_language_code": target_language,
            },
        )
        try:
            choice = response.json()["choices"][0]
            translation = choice["message"]["content"].strip()
            return translation, choice.get("finish_reason") != "stop"
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as problem:
            raise BodhanAPIError(
                "Bodhan sent a reply the app could not read.",
                status=response.status_code,
                request_id=_request_id(response),
            ) from problem

    def speak(self, text, voice, lang, style=None):
        """Read text aloud. Returns WAV bytes (PCM16, 24 kHz, mono)."""
        instructions = {"lang": lang}
        if style:
            instructions["style"] = style
        response = self._post(
            "speak",
            "/v1/audio/speech",
            json={
                "model": "indic-speak",
                "input": text,
                "voice": voice,
                "instructions": json.dumps(instructions),
            },
        )
        if not response.content.startswith(b"RIFF"):
            raise BodhanAPIError(
                "Bodhan did not send audio back.",
                status=response.status_code,
                request_id=_request_id(response),
            )
        return response.content