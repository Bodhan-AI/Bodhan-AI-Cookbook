"""Wire-protocol smoke: actual HTTP JSON/multipart and MCP subprocess, no paid calls."""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bodhan_shared.gateway import Gateway
from bodhan_shared.store import Workspace
from tests.conftest import fake_wav


class FakeBodhanHandler(BaseHTTPRequestHandler):
    observed = []

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        content = self.rfile.read(size)
        self.observed.append((self.path, content[:200]))
        if self.path == "/translate":
            payload = json.loads(content)
            body = json.dumps({"translation": payload["text"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        elif self.path == "/v1/audio/transcriptions":
            body = b'{"text":"Hello speech", "language":"en"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        elif self.path == "/v1/audio/speech":
            body = fake_wav()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
        elif self.path == "/v1/chat/completions":
            body = b'{"choices":[{"message":{"content":"A page about mathematics."}}], "blocks":[]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        else:
            self.send_response(404)
            body = b"bad endpoint"
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_four_bodhan_api_wire_formats(monkeypatch, tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeBodhanHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("BODHAN_BASE_URL", f"http://127.0.0.1:{server.server_port}")
        for cap in ["TRANSLATE", "TRANSCRIBE", "SPEAK", "OCR"]:
            monkeypatch.setenv(f"BODHAN_{cap}_KEY", "fake-test-key")
        gw = Gateway(Workspace(tmp_path))
        assert gw.translate("Hello", "ta")[0] == "Hello"
        sample = tmp_path / "audio.wav"
        sample.write_bytes(fake_wav())
        assert gw.transcribe(sample, "en", 0.8)[0]["text"] == "Hello speech"
        assert gw.speak("Hello", "Amit", "en")[0].startswith(b"RIFF")
        from PIL import Image

        picture = tmp_path / "page.png"
        Image.new("RGB", (50, 50), "white").save(picture)
        assert "mathematics" in gw.ocr(picture)[0]["choices"][0]["message"]["content"]
        paths = [x[0] for x in FakeBodhanHandler.observed[-4:]]
        assert paths == ["/translate", "/v1/audio/transcriptions", "/v1/audio/speech", "/v1/chat/completions"]
    finally:
        server.shutdown()
        server.server_close()


def test_stdio_mcp_protocol_roundtrip(tmp_path):
    cwd = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": str(cwd / "src"), "BODHAN_WORKSPACE": str(tmp_path)}
    inputs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "read_text", "arguments": {"path": "missing.txt"}}},
    ]
    p = subprocess.run([sys.executable, "-m", "bodhan_mcp"], input="\n".join(json.dumps(x) for x in inputs) + "\n",
                       text=True, capture_output=True, cwd=cwd, env=env, timeout=15)
    assert p.returncode == 0, p.stderr
    responses = [json.loads(x) for x in p.stdout.strip().splitlines()]
    assert [r["id"] for r in responses] == [1, 2, 3]
    assert len(responses[1]["result"]["tools"]) >= 8
    assert responses[2]["result"]["isError"]
