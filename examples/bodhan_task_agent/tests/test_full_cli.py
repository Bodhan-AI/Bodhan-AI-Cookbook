"""Run the real CLI against simulated OpenAI-chat and Bodhan HTTP servers, no keys billed."""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.conftest import fake_wav


class CombinedMockHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(size)
        if self.path == "/llm/chat/completions":
            data = json.loads(body)
            seen = [m for m in data["messages"] if m.get("role") == "tool"]
            if not seen:
                name, arguments = "inspect_files", {"folder": "inputs"}
            elif len(seen) == 1:
                name, arguments = "save_english_text", {
                    "path": "artifacts/summary.txt", "text": "This is an English speech summary."}
            elif len(seen) == 2:
                name, arguments = "translate_text", {
                    "target_language": "hi", "input_path": "artifacts/summary.txt"}
            elif len(seen) == 3:
                previous = json.loads(seen[-1]["content"].split(": ", 1)[-1])
                name, arguments = "speak_text", {
                    "language": "hi", "input_path": previous["output_path"]}
            else:
                name, arguments = None, None
            message = ({"role": "assistant", "content": "Generated verified output."} if name is None else
                       {"role": "assistant", "content": None, "tool_calls": [
                           {"id": f"mock_{len(seen)}", "type": "function", "function": {
                               "name": name, "arguments": json.dumps(arguments)}}]})
            response = json.dumps({"choices": [{"message": message}]}).encode()
            content_type = "application/json"
        elif self.path == "/translate":
            data = json.loads(body)
            response = json.dumps({"translation": data["text"]}).encode()
            content_type = "application/json"
        elif self.path == "/v1/audio/speech":
            response = fake_wav()
            content_type = "audio/wav"
        elif self.path == "/v1/audio/transcriptions":
            response = b'{"text":"This is an English speech summary.", "language":"hi"}'
            content_type = "application/json"
        else:
            response = b'{"error":{"code":"unknown","message":"wrong endpoint"}}'
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *args):
        pass


def test_real_cli_simulated_full_pipeline(tmp_path):
    root = Path(__file__).resolve().parents[1]
    data_dir = tmp_path / "input"
    data_dir.mkdir()
    (data_dir / "lesson.txt").write_text("A school opens at nine o'clock.")
    out_dir = tmp_path / "output"
    server = ThreadingHTTPServer(("127.0.0.1", 0), CombinedMockHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        port = server.server_port
        env = {**os.environ, "PYTHONPATH": str(root / "src"),
               "BODHAN_BASE_URL": f"http://127.0.0.1:{port}",
               "LLM_BASE_URL": f"http://127.0.0.1:{port}/llm",
               "LLM_MODEL": "mock-standard-chat", "LLM_API_KEY": "mock-key"}
        for cap in ("TRANSCRIBE", "TRANSLATE", "SPEAK", "OCR"):
            env[f"BODHAN_{cap}_KEY"] = "mock-key"
        proc = subprocess.run([sys.executable, "-m", "bodhan_agent",
             "--task", "Summarize then translate to Hindi and speak", "--files", str(data_dir),
             "--out", str(out_dir), "--max-steps", "30", "--max-cost-inr", "20"],
             cwd=root, env=env, capture_output=True, text=True, timeout=40)
        assert proc.returncode == 0, proc.stderr + "\n" + proc.stdout
        report = json.loads((out_dir / "report.json").read_text())
        assert report["status"] == "completed"
        assert any(p.endswith(".wav") for p in report["artifacts"])
        assert (out_dir / "trace.jsonl").read_text().count('"type": "tool"') >= 4
    finally:
        server.shutdown()
        server.server_close()
