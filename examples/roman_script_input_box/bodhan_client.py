"""Small Bodhan transliteration client shared by the app and the eval.

One public function, ``transliterate(text, language, script)``. Everything about
which HTTP route to call lives here, so the rest of the recipe never changes when
the route does.

Route choice (``BODHAN_TRANSLIT_ROUTE`` in .env):

* ``plain``: the documented ``POST /transliterate`` with ``{text, language, script}``,
  answer in the ``transliteration`` field.
* ``chat``:  ``POST /v1/chat/completions`` with ``model="indic-transliterate"``. On this
  route the ``language`` field is ignored (see README, "Why the chat route needs an
  instruction"), so the target script is named in the message itself.
* ``auto``: try ``plain`` once; if it answers 404, use ``chat`` for the rest of the
  process. The 404 costs no quota but was slow (1 to 24 s), so it is not the default.

The default is ``chat``. Switch to ``auto`` or ``plain`` once ``/transliterate`` is live.
"""

from __future__ import annotations

import math
import os
import queue
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

MODEL = "indic-transliterate"
POOL_SIZE = 8   # the key allows 10 requests in flight

# Language codes indic-transliterate supports (scripts/bodhan_api_rules.json), with the
# name used in the chat-route instruction. Tested end to end: hi, gu, bn, ta.
LANGUAGES = {
    "as": "Assamese",
    "bn": "Bengali",
    "brx": "Bodo",
    "doi": "Dogri",
    "gu": "Gujarati",
    "hi": "Hindi",
    "kn": "Kannada",
    "kok": "Konkani",
    "ks": "Kashmiri (Perso-Arabic)",
    "ks-Deva": "Kashmiri (Devanagari)",
    "mai": "Maithili",
    "ml": "Malayalam",
    "mni": "Manipuri (Meitei Mayek)",
    "mni-Beng": "Manipuri (Bengali)",
    "mr": "Marathi",
    "ne": "Nepali",
    "or": "Odia",
    "pa": "Punjabi (Gurmukhi)",
    "sa": "Sanskrit",
    "sat": "Santali",
    "sd": "Sindhi (Devanagari)",
    "sd-Arab": "Sindhi (Perso-Arabic)",
    "ta": "Tamil",
    "te": "Telugu",
    "ur": "Urdu",
}
SCRIPTS = ("native", "roman")


class BodhanError(Exception):
    """An API error: HTTP status, error code/type from the body, and Retry-After if sent."""

    def __init__(self, status: int, code: str, message: str, retry_after: float | None = None):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after

    @property
    def is_budget(self) -> bool:
        """A spent budget is final; a busy 429 is worth one short wait."""
        return "budget" in self.code


@dataclass
class Result:
    text: str
    latency_ms: float
    route: str            # "plain" or "chat"
    truncated: bool


def chat_instruction(text: str, language: str, script: str = "native") -> str:
    """The user turn sent on the chat route. Naming the script is what makes it work."""
    target = "roman" if script == "roman" else LANGUAGES[language]
    return f"Transliterate into {target} script: {text}"


def parse_chat(body: dict) -> tuple[str, bool]:
    """Pull the answer and a truncation flag out of a chat-completions response."""
    try:
        choice = body["choices"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise BodhanError(502, "bad_response", "no choices in the response") from exc
    content = (choice.get("message") or {}).get("content") or ""
    return content.strip(), choice.get("finish_reason") == "length"


def parse_retry_after(value: str | None) -> float | None:
    """Retry-After is either seconds or an HTTP date (RFC 9110). Bad values become None."""
    if not value:
        return None
    try:
        seconds = float(value)
        return max(seconds, 0.0) if math.isfinite(seconds) else None
    except ValueError:
        pass
    try:
        return max((parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds(), 0.0)
    except (TypeError, ValueError):
        return None


def parse_error(resp: requests.Response) -> BodhanError:
    """Turn a non-2xx response into a BodhanError. Reads only the response, never our headers.

    Documented envelope: {"error": {"message", "code", "request_id"}}. The live throttle
    sends {"error": {"type": "throttling_error", "code": "429"}}, so a descriptive type
    wins over a bare numeric code. A 404 from the gateway is {"detail": "Not Found"}.
    """
    code, message = "http_error", resp.text[:300]
    try:
        body = resp.json()
        err = body.get("error", body) if isinstance(body, dict) else {}
        if isinstance(err, dict):
            raw_code, raw_type = str(err.get("code") or ""), str(err.get("type") or "")
            code = raw_type if raw_code.isdigit() and raw_type else (raw_code or raw_type or code)
            message = str(err.get("message") or err.get("detail") or message)
    except ValueError:
        pass
    return BodhanError(resp.status_code, code, message, parse_retry_after(resp.headers.get("Retry-After")))


class Client:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, route: str | None = None,
                 timeout: float = 30, max_wait: float = 3):
        """``max_wait``: the longest a busy 429 may make us wait before the single retry.
        If Retry-After asks for more, raise at once so an interactive caller can say so."""
        self.api_key = (api_key or os.environ.get("BODHAN_API_KEY", "")).strip()
        if not self.api_key or self.api_key.startswith("<"):
            raise RuntimeError("BODHAN_API_KEY is not set. Copy .env.example to .env and paste your key.")
        self.base_url = (base_url or os.environ.get("BODHAN_BASE_URL") or "https://api.bodhan.ai").rstrip("/")
        self.route = route or os.environ.get("BODHAN_TRANSLIT_ROUTE") or "chat"
        if self.route not in {"auto", "plain", "chat"}:
            raise ValueError("BODHAN_TRANSLIT_ROUTE must be auto, plain or chat")
        self.timeout = timeout
        self.max_wait = max_wait
        self._route_lock = threading.Lock()
        # A small pool of Sessions, borrowed one per request. Each keeps its TLS connection
        # open between calls (a new connection costs ~0.6 s), and no Session is used by two
        # threads at once. The web server starts a new thread per request, so a
        # thread-local Session would never be reused there.
        self._sessions: queue.LifoQueue[requests.Session] = queue.LifoQueue()
        for _ in range(POOL_SIZE):
            s = requests.Session()
            s.headers.update({"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
            self._sessions.put(s)

    @contextmanager
    def _session(self):
        s = self._sessions.get()
        try:
            yield s
        finally:
            self._sessions.put(s)

    def _post(self, path: str, body: dict) -> requests.Response:
        """POST with at most one retry on a busy 429. A budget 429 stops at once."""
        for attempt in (1, 2):
            with self._session() as s:
                resp = s.post(f"{self.base_url}{path}", json=body, timeout=self.timeout)
            if resp.status_code != 429:
                return resp
            err = parse_error(resp)
            wait = err.retry_after if err.retry_after is not None else 2
            if err.is_budget or attempt == 2 or wait > self.max_wait:
                raise err
            # Retries count against the request limit, so wait once and give up after that.
            time.sleep(wait)
        raise AssertionError("unreachable")

    def raw_chat(self, content: str, **extra) -> Result:
        """One chat-route call with the user turn sent as-is, plus any extra body fields.
        The eval uses it to send the request without an instruction (the shape PR #39 first
        showed ignores `language`), to measure what the instruction adds."""
        started = time.perf_counter()
        resp = self._post("/v1/chat/completions", {"model": MODEL, "messages": [{"role": "user", "content": content}], **extra})
        if not resp.ok:
            raise parse_error(resp)
        out, truncated = parse_chat(resp.json())
        return Result(out, _ms(started), "chat", truncated)

    def transliterate(self, text: str, language: str, script: str = "native") -> Result:
        if language not in LANGUAGES:
            raise ValueError(f"unsupported language {language!r}")
        if script not in SCRIPTS:
            raise ValueError(f"script must be one of {SCRIPTS}")
        if not text.strip():
            return Result(text, 0.0, self.route, False)

        started = time.perf_counter()
        if self.route in {"auto", "plain"}:
            resp = self._post("/transliterate", {"text": text, "language": language, "script": script})
            if resp.status_code == 404 and self.route == "auto":
                with self._route_lock:
                    self.route = "chat"
            elif resp.ok:
                body = resp.json()
                out = str(body.get("transliteration") or "").strip()
                if not out:
                    raise BodhanError(502, "empty_answer", "the API returned an empty answer")
                return Result(out, _ms(started), "plain", bool(body.get("truncated")))
            else:
                raise parse_error(resp)

        r = self.raw_chat(chat_instruction(text, language, script))
        if not r.text:
            raise BodhanError(502, "empty_answer", "the API returned an empty answer")
        return Result(r.text, _ms(started), "chat", r.truncated)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
