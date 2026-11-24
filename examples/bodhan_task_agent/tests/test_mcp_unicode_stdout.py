"""Windows regression: MCP must emit Indic Unicode over legacy cp1252 pipes.

The wire format is ASCII-safe JSON; decoded model text remains unchanged.
"""
from __future__ import annotations

import io
import json

from bodhan_mcp import __main__ as app


class FakeHub:
    def __init__(self, *a, **kw):
        pass

    def call(self, name, arguments):
        assert name == "translate_text"
        return {"ok": True, "translation": "नमस्कार महाराष्ट्र!", "target_language": "mr"}


def test_mcp_stdout_survives_cp1252_and_retains_marathi(monkeypatch):
    request = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "translate_text", "arguments": {"target_language": "mr", "text": "Hello"}}}
    monkeypatch.setattr(app, "ToolHub", FakeHub)
    monkeypatch.setattr(app, "load_dotenv", lambda: None)
    monkeypatch.setattr(app.sys, "stdin", io.StringIO(json.dumps(request) + "\n"))
    buff = io.BytesIO()
    out = io.TextIOWrapper(buff, encoding="cp1252", write_through=True)
    monkeypatch.setattr(app.sys, "stdout", out)
    app.main()
    wire = buff.getvalue().decode("cp1252")
    assert wire.isascii()
    payload = json.loads(wire)
    assert payload["result"]["structuredContent"]["translation"] == "नमस्कार महाराष्ट्र!"
    assert json.loads(payload["result"]["content"][0]["text"])["translation"] == "नमस्कार महाराष्ट्र!"
    assert payload["result"]["isError"] is False


def test_mcp_error_response_still_has_valid_ascii_wire(monkeypatch):
    request = {"jsonrpc": "2.0", "id": 8, "method": "no-such-method"}
    monkeypatch.setattr(app, "ToolHub", FakeHub)
    monkeypatch.setattr(app, "load_dotenv", lambda: None)
    monkeypatch.setattr(app.sys, "stdin", io.StringIO(json.dumps(request) + "\n"))
    buff = io.BytesIO()
    monkeypatch.setattr(app.sys, "stdout", io.TextIOWrapper(buff, encoding="cp1252", write_through=True))
    app.main()
    payload = json.loads(buff.getvalue().decode("cp1252"))
    assert payload["error"]["code"] == -32601
