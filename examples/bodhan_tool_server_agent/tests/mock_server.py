"""Local fake of the Bodhan API plus a scripted OpenAI-compatible LLM, so e2e tests cost nothing."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from conftest import wav_bytes


def chat(text, finish="stop", toks=8):
    return {"choices": [{"finish_reason": finish, "message": {"content": text}}], "usage": {"completion_tokens": toks}}


class Mock:
    def __init__(self, script, transcript="यह एक परीक्षण है। यह दूसरा वाक्य है।", translation="This is a test.",
                 back_text=None):
        self.script, self.transcript, self.translation, self.back_text = script, transcript, translation, back_text
        self.calls: list[str] = []
        self.llm_fail = 0  # answer this many LLM requests with HTTP 500 first
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length", 0)))
                outer.calls.append(self.path)
                if self.path.startswith("/llm") and outer.llm_fail > 0:
                    outer.llm_fail -= 1
                    self.send_response(500)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                ctype, out = "application/json", {}
                if self.path == "/v1/audio/transcriptions":
                    n = outer.calls.count("/v1/audio/transcriptions") - 1
                    out = {"text": outer.transcript[min(n, len(outer.transcript) - 1)] if isinstance(outer.transcript, list) else outer.transcript}
                elif self.path == "/v1/chat/completions":
                    req = json.loads(body)
                    out = chat(outer.back_text if req.get("target_language") == "en" and outer.back_text else outer.translation)
                elif self.path == "/v1/audio/speech":
                    ctype, out = "audio/wav", wav_bytes(2.0)
                elif self.path == "/llm/chat/completions":
                    out = outer.llm(json.loads(body))
                data = out if isinstance(out, bytes) else json.dumps(out).encode()
                self.send_response(200)
                self.send_header("content-type", ctype)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def llm(self, req):
        msgs = req["messages"]
        done = [tc["function"]["name"] for m in msgs if m.get("tool_calls") for tc in m["tool_calls"]]
        last = next((m["content"] for m in reversed(msgs) if m["role"] == "tool"), "")
        first_user = msgs[1]["content"]
        name, args = self.script(done, last, first_user)
        if name is None:  # the model answers in prose and calls no tool
            msg, reason = {"role": "assistant", "content": "" if args.get("empty") else "I cannot reveal secrets."}, "stop"
        else:
            msg, reason = {"role": "assistant", "content": f"next: {name}",
                           "tool_calls": [{"id": f"c{len(done)}", "type": "function",
                                           "function": {"name": name, "arguments": json.dumps(args)}}]}, "tool_calls"
        return {"id": "x", "object": "chat.completion", "created": 0, "model": "fake",
                "choices": [{"index": 0, "finish_reason": reason, "message": msg}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}


def ref(text, key):
    m = re.search(rf'"{key}": "([^"]+)"', text)
    return m.group(1) if m else None
