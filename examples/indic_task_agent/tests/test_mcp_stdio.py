"""The server, launched as its own process, driven by the official MCP Python client
over stdio. No direct imports of the tool code: this is what a third-party client sees."""

import json
import sys

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters


def server(tmp_path, *extra):
    env = {"PYTHONIOENCODING": "utf-8"}  # deliberately no Bodhan keys
    return StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp", "--artifacts-dir", str(tmp_path / "art"), "--no-cache", *extra],
                                 env=env, cwd=str(tmp_path))


async def test_tool_catalogue_is_self_describing(tmp_path):
    async with Client(server(tmp_path)) as c:
        tools = (await c.list_tools()).tools
        names = {t.name for t in tools}
        assert {"transcribe_audio", "translate_text", "synthesize_speech", "ocr_document", "create_subtitles", "inspect_files"} <= names
        for t in tools:
            assert len(t.description) > 60, t.name
            assert t.input_schema["type"] == "object"
            for prop, spec in t.input_schema["properties"].items():
                assert spec.get("description"), f"{t.name}.{prop} has no description"
            assert "$ref" not in json.dumps(t.input_schema)


async def test_errors_are_structured_and_server_survives(tmp_path):
    async with Client(server(tmp_path)) as c:
        cases = [
            ("translate_text", {"text": "hello", "target_language": "fr"}, "unsupported_language"),
            ("translate_text", {"target_language": "hi"}, "invalid_argument"),
            ("translate_text", {"text": "x", "target_language": "hi", "unknown_field": 1}, "invalid_argument"),
            ("ocr_document", {"path": str(tmp_path / "missing.png")}, "file_not_found"),
            ("synthesize_speech", {"text": "নমস্কার", "language": "bn", "voice": "Nobody"}, "unsupported_voice"),
            ("transcribe_audio", {"path": 42}, "invalid_argument"),
            ("no_such_tool", {}, "unknown_tool"),
            ("translate_text", {"text": "Hello", "target_language": "ta"}, "missing_credentials"),
        ]
        for name, args, code in cases:
            r = await c.call_tool(name, args)
            assert r.is_error, (name, args)
            payload = r.structured_content or json.loads(r.content[0].text)
            assert payload["ok"] is False and payload["error"]["code"] == code, payload
            assert payload["error"]["message"]
        ok = await c.call_tool("detect_language", {"text": "ఇది తెలుగు వాక్యం"})  # still alive after bad input
        assert not ok.is_error and ok.structured_content["detection"]["language"] == "te"


async def test_free_tools_and_resources(tmp_path):
    (tmp_path / "note.txt").write_text("यह एक सूचना है।", encoding="utf-8")
    async with Client(server(tmp_path)) as c:
        r = await c.call_tool("inspect_files", {"paths": [str(tmp_path / "note.txt")]})
        assert not r.is_error and r.structured_content["files"][0]["language"]["language"] == "hi"
        r = await c.call_tool("list_voices", {"language": "Bengali"})
        assert r.structured_content["voices"]["female"] == ["Ishita"]
        r = await c.call_tool("write_text", {"name": "summary.md", "content": "An English summary."})
        assert not r.is_error and r.structured_content["path"].endswith("summary.md")
        resources = await c.list_resources()
        assert {str(x.uri) for x in resources.resources} >= {"bodhan://voices", "bodhan://languages"}
        voices = await c.read_resource("bodhan://voices")
        assert "Anitha" in voices.contents[0].text


async def test_allow_dir_sandbox(tmp_path):
    inside = tmp_path / "inputs"
    inside.mkdir()
    (tmp_path / "private.txt").write_text("secret", encoding="utf-8")
    async with Client(server(tmp_path, "--allow-dir", str(inside))) as c:
        r = await c.call_tool("read_text", {"path": str(tmp_path / "private.txt")})
        assert r.is_error and r.structured_content["error"]["code"] == "path_not_allowed"


async def test_unknown_resource_is_a_protocol_error_and_server_survives(tmp_path):
    from mcp.shared.exceptions import MCPError

    async with Client(server(tmp_path)) as c:
        try:
            await c.read_resource("bodhan://does-not-exist")
            raised = None
        except MCPError as exc:
            raised = exc
        assert raised is not None and raised.error.code == -32602 and "does-not-exist" in raised.error.message
        ok = await c.read_resource("bodhan://voices")
        assert "Anitha" in ok.contents[0].text
