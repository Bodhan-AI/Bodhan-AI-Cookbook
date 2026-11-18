"""Local HTTP fakes for end-to-end tests: a Bodhan API (used by the MCP server subprocess) and an
OpenAI-compatible LLM driven by a scripted "brain" function. Both run in threads on 127.0.0.1."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx


class _Server:
    def __init__(self, handler_fn):
        fn = handler_fn

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                status, headers, content = fn(self.command, self.path, dict(self.headers), body)
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("content-length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            do_GET = do_POST  # noqa: N815

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def bodhan_server(fake) -> _Server:
    """Serve a conftest.FakeBodhan over real HTTP."""
    routes = {"/v1/audio/transcriptions": fake.transcribe, "/v1/audio/speech": fake.speak,
              "/v1/chat/completions": fake.chat}

    def handle(method, path, headers, body):
        fn = routes.get(path.split("?")[0])
        if not fn:
            return 404, {"content-type": "application/json"}, b'{"detail":"Not Found"}'
        req = httpx.Request(method, f"http://fake{path}", headers=headers, content=body)
        resp = fn(req)
        return resp.status_code, {"content-type": resp.headers.get("content-type", "application/json")}, resp.content

    return _Server(handle)


class FakeLLM:
    """OpenAI-compatible /chat/completions. `brain(request_json) -> dict` returns either
    {"content": str} or {"tool_calls": [(name, args), ...]} or {"status": 429, "message": ...}."""

    def __init__(self, brain):
        self.brain = brain
        self.requests: list[dict] = []
        self.server = _Server(self._handle)

    def _handle(self, method, path, headers, body):
        if not path.endswith("/chat/completions"):
            return 404, {"content-type": "application/json"}, b"{}"
        req = json.loads(body)
        self.requests.append(req)
        out = self.brain(req)
        if out.get("status"):
            err = {"error": {"message": out.get("message", "error"), "type": "rate_limit", "code": str(out["status"])}}
            return out["status"], {"content-type": "application/json"}, json.dumps(err).encode()
        msg = {"role": "assistant", "content": out.get("content", "")}
        if out.get("tool_calls"):
            msg["tool_calls"] = [{"id": f"call_{len(self.requests)}_{i}", "type": "function",
                                  "function": {"name": n, "arguments": json.dumps(a, ensure_ascii=False)}}
                                 for i, (n, a) in enumerate(out["tool_calls"])]
        resp = {"id": f"x{len(self.requests)}", "object": "chat.completion", "created": int(time.time()),
                "model": req.get("model"), "choices": [{"index": 0, "message": msg,
                                                        "finish_reason": "tool_calls" if out.get("tool_calls") else "stop"}],
                "usage": {"prompt_tokens": len(body) // 4, "completion_tokens": 20, "total_tokens": len(body) // 4 + 20}}
        return 200, {"content-type": "application/json"}, json.dumps(resp).encode()

    def __enter__(self):
        self.server.__enter__()
        return self

    def __exit__(self, *exc):
        self.server.__exit__(*exc)

    @property
    def url(self) -> str:
        return self.server.url + "/v1"


# --------------------------------------------------------------------------- brain helpers


def last_tool(req: dict) -> tuple[str | None, dict]:
    """Name and parsed content of the most recent tool message."""
    for m in reversed(req["messages"]):
        if m["role"] == "tool":
            try:
                return m.get("name"), json.loads(m["content"])
            except (json.JSONDecodeError, TypeError):
                return m.get("name"), {"raw": m["content"]}
        if m["role"] == "user":
            return None, {}
    return None, {}


def is_plan(req: dict) -> bool:
    return req.get("response_format", {}).get("type") == "json_object" or "Return ONLY a JSON object" in str(
        req["messages"][0].get("content", ""))


def is_summary(req: dict) -> bool:
    return "summarise English text faithfully" in str(req["messages"][0].get("content", ""))


def manifest_paths(req: dict) -> list[str]:
    import re

    text = " ".join(str(m.get("content", "")) for m in req["messages"] if m["role"] == "user")
    return re.findall(r'"path": "([^"]+)"', text)
