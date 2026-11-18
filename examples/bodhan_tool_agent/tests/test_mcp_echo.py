"""Test verifying MCP server tool list and comprehensive tool descriptions for third-party clients."""

import json
import os
import sys
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

EXPECTED_MCP_TOOLS = [
    "indic_translate",
    "indic_ocr",
    "indic_transcribe",
    "indic_speak",
    "inspect_media",
    "read_text_file",
    "make_srt",
    "detect_script_language",
]


@pytest.mark.asyncio
async def test_mcp_server_exact_tools_and_descriptions():
    """Verify exact 8 tools exposed on MCP server, absence of echo/finish, and sufficiency of descriptions."""
    env = os.environ.copy()
    src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    env["PYTHONPATH"] = src_dir
    env.pop("BODHAN_ALLOW_LIVE", None)

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "bodhan_mcp"],
        env=env,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            init_result = await session.initialize()
            assert init_result is not None

            # 1. List tools
            tools_response = await session.list_tools()
            tool_map = {tool.name: tool for tool in tools_response.tools}
            tool_names = list(tool_map.keys())

            # Assert exact list of tools
            assert set(tool_names) == set(EXPECTED_MCP_TOOLS)
            assert len(tool_names) == 8
            assert "echo" not in tool_names
            assert "finish" not in tool_names

            # 2. Check each tool description is self-contained and sufficient for third-party clients
            for expected_name in EXPECTED_MCP_TOOLS:
                tool = tool_map[expected_name]
                desc = tool.description or ""
                assert len(desc) > 80, (
                    f"Description for {expected_name} too short ({len(desc)} chars)"
                )
                assert "Args:" in desc or "args" in desc.lower() or "parameters" in desc.lower()

            # Tool-specific description content assertions
            assert "English" in tool_map["indic_translate"].description
            assert "PDF" in tool_map["indic_ocr"].description
            assert "audio" in tool_map["indic_transcribe"].description.lower()
            assert "voice" in tool_map["indic_speak"].description.lower()
            assert "path" in tool_map["inspect_media"].description.lower()
            assert "text" in tool_map["read_text_file"].description.lower()
            assert "srt" in tool_map["make_srt"].description.lower()
            assert "script" in tool_map["detect_script_language"].description.lower()

            # 3. Test calling detect_script_language over MCP stdio
            res = await session.call_tool("detect_script_language", arguments={"text": "नमस्ते भारत"})
            assert res is not None
            assert len(res.content) > 0
            res_data = json.loads(res.content[0].text)
            assert res_data.get("success") is True
            assert "hi" in res_data.get("language", "")
