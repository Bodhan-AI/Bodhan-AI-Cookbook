"""Roman-script input box: type Hinglish (or Gujlish, Tanglish...) and send it in native script.

    python app.py            # then open http://127.0.0.1:8000

A small standard-library web server. It serves static/index.html and one JSON endpoint,
so the Bodhan key stays on the server and never reaches the browser.

    POST /api/transliterate  {"text": "...", "language": "hi", "mode": "word"|"draft"|"sentence", "script": "native"|"roman"}
    GET  /api/languages

The server spends your API quota, so it only answers its own page: requests must carry a
local Host header (blocks DNS rebinding), JSON content type (forces a CORS preflight,
which is never granted) and no cross-site Origin. It only ever binds to 127.0.0.1.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

from bodhan_client import LANGUAGES, SCRIPTS, BodhanError, Client  # noqa: E402  (after load_dotenv)
from converter import Converter, TooManyWords  # noqa: E402

MAX_CHARS = 500          # one chat message, not a document
MAX_BODY = 8 * 1024
MODES = {"word", "draft", "sentence"}
LOCAL_HOSTS = {"127.0.0.1", "localhost"}
INDEX = (HERE / "static" / "index.html").read_bytes()

log = logging.getLogger("roman_input_box")


class Handler(BaseHTTPRequestHandler):
    server_version = "roman-input-box"
    converter: Converter          # set in main(), or by tests

    # ---- request guards -------------------------------------------------------------

    def _local_request(self) -> bool:
        """Only this machine's own page may use the server (and so the key)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
        if host not in LOCAL_HOSTS:
            return False
        origin = self.headers.get("Origin")
        if origin and origin.split("://", 1)[-1].rsplit(":", 1)[0].lower() not in LOCAL_HOSTS:
            return False
        return self.headers.get("Sec-Fetch-Site", "same-origin") in {"same-origin", "none"}

    def _read_json(self) -> dict | None:
        """Parse a bounded JSON object body, or answer the error and return None."""
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "send Content-Type: application/json"})
            return None
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._json(HTTPStatus.LENGTH_REQUIRED, {"error": "Content-Length required"})
            return None
        if not 0 <= length <= MAX_BODY:
            self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request too large"})
            return None
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = None
        if not isinstance(body, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "body must be a JSON object"})
            return None
        return body

    # ---- routes ---------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if not self._local_request():
            return self._json(HTTPStatus.FORBIDDEN, {"error": "local use only"})
        if self.path in {"/", "/index.html"}:
            self._send(HTTPStatus.OK, INDEX, "text/html; charset=utf-8")
        elif self.path == "/api/languages":
            self._json(HTTPStatus.OK, {"languages": LANGUAGES, "route": self.converter.client.route})
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._local_request():
            return self._json(HTTPStatus.FORBIDDEN, {"error": "local use only"})
        if self.path != "/api/transliterate":
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        req = self._read_json()
        if req is None:
            return
        text, language = req.get("text"), req.get("language", "hi")
        mode, script = req.get("mode", "sentence"), req.get("script", "native")
        if not all(isinstance(v, str) for v in (text, language, mode, script)):
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "text, language, mode and script must be strings"})
        if language not in LANGUAGES or script not in SCRIPTS or mode not in MODES:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "bad language, script or mode"})
        if len(text) > MAX_CHARS:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": f"keep messages under {MAX_CHARS} characters"})

        try:
            c = self.converter.convert(text, language, mode, script)
        except TooManyWords as exc:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except BodhanError as exc:
            if exc.status == 429:
                wait = f" Try again in {exc.retry_after:.0f} s." if exc.retry_after else " Try again shortly."
                message = ("Bodhan budget for this key is used up." if exc.is_budget
                           else "Bodhan is throttling this key." + wait)
                return self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": message, "code": exc.code})
            return self._json(HTTPStatus.BAD_GATEWAY, {"error": exc.message, "code": exc.code, "upstream_status": exc.status})
        except requests.RequestException as exc:
            return self._json(HTTPStatus.BAD_GATEWAY, {"error": f"could not reach Bodhan ({type(exc).__name__})"})
        except Exception:
            # A bug, not a network problem: log the traceback (never the user's text) and say so.
            log.exception("unexpected error in /api/transliterate (mode=%s, language=%s)", mode, language)
            return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal error, see the server log"})

        self._json(HTTPStatus.OK, {
            "text": c.text, "mode": c.mode, "latency_ms": c.latency_ms, "calls": c.calls,
            "cached": c.cached, "truncated": c.truncated, "route": self.converter.client.route,
        })

    # ---- helpers --------------------------------------------------------------------

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        # Logs method, path and status only. Request bodies (what people type) are never logged.
        if os.environ.get("QUIET") != "1":
            super().log_message(fmt, *args)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        Handler.converter = Converter(Client())
    except (RuntimeError, ValueError) as exc:
        print(f"Setup problem: {exc}", file=sys.stderr)
        return 1
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    if host not in {"127.0.0.1", "localhost"}:
        # The Host/Origin checks stop browsers, not scripts: on a network address anyone
        # could spend this key with curl. Serving others needs your own authenticated backend.
        print(f"Setup problem: HOST={host} is not supported; this demo serves 127.0.0.1 only.", file=sys.stderr)
        return 1
    print(f"Roman-script input box on http://{host}:{port}  (Ctrl+C to stop)")
    try:
        ThreadingHTTPServer((host, port), Handler).serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
