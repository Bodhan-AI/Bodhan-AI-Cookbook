"""Start the real server as a subprocess and talk to it over stdio with the SDK's own client.

This is what an off-the-shelf MCP client does, so it checks that schemas and
errors are usable without our agent.
"""

import os
import sys

import anyio
from mcp import Client, StdioServerParameters


def _params(tmp_path) -> StdioServerParameters:
    env = {
        **os.environ,
        "BODHAN_MCP_ROOTS": str(tmp_path),
        "BODHAN_MCP_OUTPUT_DIR": str(tmp_path / "outputs"),
        "BODHAN_CACHE_DIR": str(tmp_path / "cache"),
        "BODHAN_API_KEY": "",
        "BODHAN_TRANSLATE_KEY": "",
        "BODHAN_LOG_LEVEL": "ERROR",
    }
    return StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp"], env=env, cwd=str(tmp_path))


def test_server_lists_tools_and_returns_structured_errors(tmp_path):
    (tmp_path / "note.txt").write_text("Good morning", encoding="utf-8")

    async def scenario():
        async with Client(_params(tmp_path)) as client:
            listing = await client.list_tools()
            tools = {t.name: t for t in listing.tools}
            assert {"transcribe_audio", "translate_text", "synthesize_speech", "ocr_document", "inspect_files", "list_capabilities"} <= set(tools)
            for tool in tools.values():
                assert tool.description and len(tool.description) > 60
                assert tool.input_schema["type"] == "object"
                assert tool.output_schema["required"] == ["ok"]
                assert all("description" in prop for prop in tool.input_schema.get("properties", {}).values())
            assert tools["translate_text"].input_schema["required"] == ["target_language"]

            ok = await client.call_tool("list_capabilities", {})
            assert not ok.is_error and ok.structured_content["speech_languages_and_voices"]["ta"]["female"] == ["Anitha"]

            files = await client.call_tool("inspect_files", {})
            assert files.structured_content["files"][0]["kind"] == "text"

            cases = {
                "unsupported_language": ("translate_text", {"text": "Good morning", "target_language": "French"}),
                "invalid_arguments": ("translate_text", {"text": "Good morning"}),
                "file_not_found": ("transcribe_audio", {"path": "nope.wav"}),
                "unsupported_file_type": ("transcribe_audio", {"path": "note.txt"}),
                "missing_api_key": ("translate_text", {"source_path": "note.txt", "target_language": "hi"}),
                "path_not_allowed": ("read_text", {"path": sys.executable}),  # a real file outside the allowed folder
                "unknown_tool": ("no_such_tool", {}),
            }
            for code, (name, arguments) in cases.items():
                result = await client.call_tool(name, arguments)
                assert result.is_error, name
                error = result.structured_content["error"]
                assert error["code"] == code and error["message"], (name, error)

            # The server is still alive after all those bad calls.
            assert not (await client.call_tool("list_capabilities", {})).is_error

            resources = await client.list_resources()
            templates = await client.list_resource_templates()
            assert templates.resource_templates == []  # answered, not an error
            assert {str(r.uri) for r in resources.resources} == {"bodhan://voices", "bodhan://languages"}
            voices = await client.read_resource("bodhan://voices")
            assert "Kavya" in voices.contents[0].text

    anyio.run(scenario)
