"""Integration test driving `python -m bodhan_mcp` over stdio using the official MCP client SDK."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def test_mcp_stdio_lists_tools_resources_and_handles_bad_inputs(tmp_path: Path) -> None:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "bodhan_mcp", "--work-dir", str(tmp_path / "mcp_work")],
    )
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools_resp = await session.list_tools()
            tool_names = {t.name for t in tools_resp.tools}
            expected = {
                "inspect_files",
                "detect_language",
                "transcribe_audio",
                "translate_text",
                "synthesize_speech",
                "verify_speech",
                "ocr_document",
                "transliterate_text",
                "write_subtitles",
            }
            assert expected.issubset(tool_names)
            # Every tool must have a non-trivial self-contained description and JSON schema.
            for tool in tools_resp.tools:
                assert tool.description and len(tool.description) >= 40
                schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
                assert schema is not None

            # Resources exposed.
            res_list = await session.list_resources()
            uris = {str(r.uri) for r in res_list.resources}
            assert {"bodhan://languages", "bodhan://voices", "bodhan://pricing"}.issubset(uris)

            lang_res = await session.read_resource("bodhan://languages")
            lang_data = json.loads(lang_res.contents[0].text)
            assert "ta" in lang_data["by_model"]["indic-translate"]

            # Prompt exposed.
            prompts = await session.list_prompts()
            assert any(p.name == "plan_task" for p in prompts.prompts)

            # Free tool call works over stdio.
            sample_txt = tmp_path / "sample.txt"
            sample_txt.write_text("தமிழ் மொழி இனிமையானது.", encoding="utf-8")
            inspect_out = await session.call_tool("inspect_files", {"paths": [str(sample_txt)]})
            payload = json.loads(inspect_out.content[0].text)
            assert payload["ok"] is True
            assert payload["data"]["files"][0]["detected_language_hint"] == "ta"

            # Bad input returns structured error and keeps server alive!
            bad_out = await session.call_tool("translate_text", {"target_language": "fr", "text": "Bonjour"})
            bad_payload = json.loads(bad_out.content[0].text)
            assert bad_payload["ok"] is False
            assert bad_payload["error"]["code"] == "unsupported_language"

            # Server still responds cleanly after bad input.
            follow_up = await session.call_tool("detect_language", {"text": "ఇది తెలుగు."})
            follow_payload = json.loads(follow_up.content[0].text)
            assert follow_payload["ok"] is True
            assert follow_payload["data"]["language"] == "te"
