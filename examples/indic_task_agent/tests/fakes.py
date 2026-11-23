"""A fake Bodhan backend (and a scripted fake LLM) so tests run offline and without keys."""

from __future__ import annotations

import io
import json
import math
import re
import struct
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


def tone_wav(seconds: float, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 300 * i / rate))) for i in range(int(seconds * rate))))
    return buf.getvalue()


def wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / w.getframerate()


class FakeBodhan:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.asr = lambda audio, language: "नमस्ते यह एक परीक्षण है।"
        self.truncate_over: int | None = None  # translations of longer inputs come back truncated

    def handle(self, route: str, body: dict | None = None, form: dict | None = None, audio: bytes | None = None) -> tuple[int, bytes]:
        self.calls.append((route, body or form or {}))
        if route == "/v1/audio/transcriptions":
            if audio and audio[:4] == b"RIFF" and wav_seconds(audio) > 30:
                return 400, json.dumps({"error": {"code": "audio_too_long", "message": "over 30 s"}}).encode()
            return 200, json.dumps({"text": self.asr(audio, (form or {}).get("language"))}, ensure_ascii=False).encode()
        if route == "/v1/chat/completions" and body.get("model") == "indic-translate":
            text, target = body["messages"][0]["content"], body["target_language"]
            if self.truncate_over and len(text) > self.truncate_over:
                return 200, json.dumps(
                    {"choices": [{"finish_reason": "length", "message": {"content": "Partial"}}], "usage": {"completion_tokens": 2}}
                ).encode()
            word = "translated " if target == "en" else "अनुवाद "
            out = (word * max(1, round(len(text) / len(word)))).strip()
            return 200, json.dumps(
                {"choices": [{"finish_reason": "stop", "message": {"content": out}}], "usage": {"completion_tokens": len(out) // 4}}, ensure_ascii=False
            ).encode()
        if route == "/v1/audio/speech":
            return 200, tone_wav(min(len(body["input"]) / 12, 25))
        if route == "/v1/chat/completions" and body.get("model") == "indic-ocr":
            md = "पाठ एक\n\n| नाम | अंक |\n|---|---|\n| राम | १० |"
            return 200, json.dumps({"choices": [{"message": {"content": md}}], "blocks": []}, ensure_ascii=False).encode()
        if route == "/v1/chat/completions":
            return 200, json.dumps(scripted_llm(body["messages"])).encode()
        return 404, b'{"error": {"code": "not_found", "message": "no route"}}'

    async def post(self, url, headers, timeout, json=None, data=None, files=None):
        """Drop-in replacement for bodhan_mcp.bodhan._post."""
        assert headers["Authorization"].startswith("Bearer ")
        audio = files["file"][1] if files else None
        status, body = self.handle(urlparse(url).path, json, data, audio)
        return status, {}, body


def _tool_call(name: str, args: dict) -> dict:
    return {
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": f"call_{name}", "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}],
                },
            }
        ],
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": "fake",
        "usage": {"total_tokens": 10},
    }


def _tool_json(messages: list[dict], name: str) -> dict | None:
    for m in messages:
        if m.get("role") == "tool" and f'tool="{name}"' in m["content"]:
            return json.loads(m["content"].split("\n", 1)[1].rsplit("\n</tool_output>", 1)[0])
    return None


def scripted_llm(messages: list[dict]) -> dict:
    """Plan, transcribe the first input, translate the transcript to English, finish."""
    user = messages[1]["content"]
    artifacts = re.search(r"ARTIFACTS folder \(deliverables\): (.+)", user)[1].strip()
    if not any(m.get("role") == "tool" for m in messages):
        return _tool_call("update_plan", {"steps": ["1. transcribe", "2. translate to English", "3. finish"], "assumptions": ["The clip is Hindi."]})
    transcript = _tool_json(messages, "transcribe_audio")
    if transcript is None:
        return _tool_call("transcribe_audio", {"path": re.search(r"^- (/\S+)", user, re.M)[1]})
    if _tool_json(messages, "translate_text") is None:
        return _tool_call("translate_text", {"path": transcript["text_path"], "target_language": "en", "output_path": f"{artifacts}/transcript_en.md"})
    return _tool_call("finish", {"status": "success", "answer": "Transcribed the clip and translated it to English.", "artifacts": ["transcript_en.md"]})


class FakeServer:
    """Serve FakeBodhan over HTTP for subprocess tests."""

    def __init__(self, fake: FakeBodhan):
        outer = fake

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                if "multipart" in self.headers.get("Content-Type", ""):
                    lang = re.search(rb'name="language"\r\n\r\n(\w+)', raw)
                    status, body = outer.handle(self.path, form={"language": lang[1].decode()} if lang else {}, audio=None)
                else:
                    status, body = outer.handle(self.path, body=json.loads(raw))
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
