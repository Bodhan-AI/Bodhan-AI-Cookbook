"""Exercise both officially documented Indic-Translate transports without live API calls."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bodhan_shared.errors import TaskError
from bodhan_shared.gateway import Gateway
from bodhan_shared.store import Workspace


class TranslationFallbackHandler(BaseHTTPRequestHandler):
    seen = []
    chat_status = 200

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        self.seen.append((self.path, body))
        if self.path == "/translate":
            status, response = 404, {"error": {"message": "not found", "code": "unknown"}}
        elif self.path == "/v1/chat/completions":
            if self.chat_status == 200:
                status, response = 200, {"choices": [{"message": {"content": "বাংলা লেখা"}}]}
            else:
                status, response = self.chat_status, {"error": {"message": "unavailable", "code": "unknown"}}
        else:
            status, response = 500, {}
        payload = json.dumps(response).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("x-request-id", "sample-123")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


@pytest.fixture
def fallback_server(monkeypatch):
    TranslationFallbackHandler.seen = []
    TranslationFallbackHandler.chat_status = 200
    server = ThreadingHTTPServer(("127.0.0.1", 0), TranslationFallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("BODHAN_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "test-only-key")
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()


def test_404_product_route_falls_back_to_documented_chat_route(fallback_server, tmp_path):
    gw = Gateway(Workspace(tmp_path))
    translated, cached = gw.translate("Hello", "bn")
    assert translated == "বাংলা লেখা"
    assert not cached
    assert [path for path, _ in TranslationFallbackHandler.seen] == [
        "/translate", "/v1/chat/completions"]
    chat = TranslationFallbackHandler.seen[1][1]
    assert chat == {"model": "indic-translate", "messages": [{"role": "user", "content": "Hello"}],
                    "target_language_code": "bn"}
    assert [c["endpoint"] for c in gw.calls] == ["/v1/chat/completions"]
    again, cache_hit = gw.translate("Hello", "bn")
    assert again == translated and cache_hit
    assert len(TranslationFallbackHandler.seen) == 2


def test_both_routes_404_report_support_info(fallback_server, tmp_path):
    TranslationFallbackHandler.chat_status = 404
    gw = Gateway(Workspace(tmp_path))
    with pytest.raises(TaskError) as exc:
        gw.translate("Hello", "bn")
    assert exc.value.code == "BODHAN_TRANSLATE_UNAVAILABLE"
    assert "sample-123" in str(exc.value)
    assert "Both documented" in str(exc.value)
