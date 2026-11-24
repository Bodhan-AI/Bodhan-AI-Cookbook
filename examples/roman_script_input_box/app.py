"""Roman-script input box: type Hinglish (or Gujlish, Tanglish...) and send it in native script.

    python app.py            # then open http://127.0.0.1:8000

A small standard-library web server. It serves static/index.html and one JSON endpoint,
so the Bodhan key stays on the server and never reaches the browser.

    POST /api/transliterate  {"text": "...", "language": "hi", "mode": "word"|"draft"|"sentence", "script": "native"|"roman"}
    GET  /api/languages
"""

from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

from bodhan_client import LANGUAGES, SCRIPTS, BodhanError, Client  # noqa: E402  (after load_dotenv)
from converter import Converter  # noqa: E402

MAX_CHARS = 500          # one chat message, not a document
MAX_BODY = 8 * 1024
INDEX = (HERE / "static" / "index.html").read_bytes()

converter = Converter(Client())


class Handler(BaseHTTPRequestHandler):
    server_version = "roman-input-box"

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/", "/index.html"}:
            self._send(HTTPStatus.OK, INDEX, "text/html; charset=utf-8")
        elif self.path == "/api/languages":
            self._json(HTTPStatus.OK, {"languages": LANGUAGES, "route": converter.client.route})
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/transliterate":
            return self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request too large"})
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
            text = str(req.get("text", ""))
            language = str(req.get("language", "hi"))
            mode = str(req.get("mode", "sentence"))
            script = str(req.get("script", "native"))
        except (ValueError, AttributeError):
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "body must be a JSON object"})

        if language not in LANGUAGES or script not in SCRIPTS or mode not in {"word", "draft", "sentence"}:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": "bad language, script or mode"})
        if len(text) > MAX_CHARS:
            return self._json(HTTPStatus.BAD_REQUEST, {"error": f"keep messages under {MAX_CHARS} characters"})

        try:
            c = converter.convert(text, language, mode, script)
        except BodhanError as exc:
            status = HTTPStatus.TOO_MANY_REQUESTS if exc.status == 429 else HTTPStatus.BAD_GATEWAY
            message = exc.message
            if exc.status == 429 and exc.retry_after:
                message = f"Bodhan is throttling this key. Try again in {exc.retry_after:.0f} s."
            return self._json(status, {"error": message, "code": exc.code, "upstream_status": exc.status})
        except Exception as exc:  # network errors, timeouts
            return self._json(HTTPStatus.BAD_GATEWAY, {"error": f"{type(exc).__name__}: could not reach Bodhan"})

        self._json(HTTPStatus.OK, {
            "text": c.text, "mode": c.mode, "latency_ms": c.latency_ms, "calls": c.calls,
            "cached": c.cached, "truncated": c.truncated, "route": converter.client.route,
        })

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        # Log method, path and status only. Request bodies (what people type) are not logged.
        if os.environ.get("QUIET") != "1":
            super().log_message(fmt, *args)


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    print(f"Roman-script input box on http://{host}:{port}  (Ctrl+C to stop)")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
