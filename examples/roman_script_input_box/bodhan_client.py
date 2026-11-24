"""Small Bodhan transliteration client shared by the app and the eval.

One public function, ``transliterate(text, language, script)``. Everything about
which HTTP route to call lives here, so the rest of the recipe never changes when
the route does.

Route choice (``BODHAN_TRANSLIT_ROUTE`` in .env):

* ``plain``: the documented ``POST /transliterate`` with ``{text, language, script}``.
* ``chat``:  ``POST /v1/chat/completions`` with ``model="indic-transliterate"``. On this
  route the ``language`` field is ignored (see README, "Why the chat route needs an
  instruction"), so the target script is named in the message itself.
* ``auto``: try ``plain`` once; if it answers 404, use ``chat`` for the rest of the
  process. The 404 costs no quota but was slow (1 to 24 s), so it is not the default.

The default is ``chat``. Switch to ``auto`` or ``plain`` once ``/transliterate`` is live.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

import requests

MODEL = "indic-transliterate"

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
    """An API error with the HTTP status and the error code from the body, if any."""

    def __init__(self, status: int, code: str, message: str, retry_after: float | None = None):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after


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
    choice = body["choices"][0]
    content = (choice.get("message") or {}).get("content") or ""
    return content.strip(), choice.get("finish_reason") == "length"


def parse_error(resp: requests.Response) -> BodhanError:
    """Turn a non-2xx response into a BodhanError without ever echoing request headers."""
    code, message = "http_error", resp.text[:300]
    try:
        body = resp.json()
        err = body.get("error", body) if isinstance(body, dict) else {}
        if isinstance(err, dict):
            # Prefer the descriptive type ("throttling_error") over a bare "429" code.
            code = str(err.get("type") or err.get("code") or code)
            message = str(err.get("message") or err.get("detail") or message)
    except ValueError:
        pass
    retry_after = resp.headers.get("Retry-After")
    return BodhanError(resp.status_code, code, message, float(retry_after) if retry_after else None)


class Client:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, route: str | None = None,
                 timeout: float = 30, max_wait: float = 3):
        """``max_wait``: the longest a busy 429 may make us wait before the single retry.
        If Retry-After asks for more, raise at once so an interactive caller can say so."""
        self.api_key = api_key or os.environ.get("BODHAN_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("BODHAN_API_KEY is not set. Copy .env.example to .env and paste your key.")
        self.base_url = (base_url or os.environ.get("BODHAN_BASE_URL") or "https://api.bodhan.ai").rstrip("/")
        self.route = route or os.environ.get("BODHAN_TRANSLIT_ROUTE") or "chat"
        if self.route not in {"auto", "plain", "chat"}:
            raise ValueError("BODHAN_TRANSLIT_ROUTE must be auto, plain or chat")
        self.timeout = timeout
        self.max_wait = max_wait
        # One session per thread: keeps the TLS connection open (the first call on a new
        # connection is ~0.6 s slower) without sharing a Session across threads.
        self._local = threading.local()
        self._route_lock = threading.Lock()

    @property
    def _session(self) -> requests.Session:
        if not hasattr(self._local, "session"):
            s = requests.Session()
            s.headers.update({"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
            self._local.session = s
        return self._local.session

    def _post(self, path: str, body: dict) -> requests.Response:
        """POST with at most one retry on a busy 429. A budget 429 stops at once."""
        for attempt in (1, 2):
            resp = self._session.post(f"{self.base_url}{path}", json=body, timeout=self.timeout)
            if resp.status_code != 429:
                return resp
            err = parse_error(resp)
            wait = err.retry_after if err.retry_after is not None else 2
            if "budget" in err.code or attempt == 2 or wait > self.max_wait:
                raise err
            # Retries count against the request limit, so wait once and give up after that.
            time.sleep(wait)
        raise AssertionError("unreachable")

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
                out = body.get("text") or body.get("output") or body.get("transliteration") or ""
                return Result(out.strip(), _ms(started), "plain", bool(body.get("truncated")))
            else:
                raise parse_error(resp)

        resp = self._post("/v1/chat/completions", {
            "model": MODEL,
            "messages": [{"role": "user", "content": chat_instruction(text, language, script)}],
        })
        if not resp.ok:
            raise parse_error(resp)
        out, truncated = parse_chat(resp.json())
        return Result(out, _ms(started), "chat", truncated)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
