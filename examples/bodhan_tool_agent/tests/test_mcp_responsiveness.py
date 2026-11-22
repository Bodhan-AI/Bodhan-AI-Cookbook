import asyncio
import unittest
from threading import Event
from unittest.mock import patch

import httpx
from bodhan_mcp.ocr import extract_document_text
from bodhan_mcp.server import mcp, translate_text
from bodhan_mcp.speech import synthesize_speech
from bodhan_mcp.transcription import transcribe_audio
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session


class McpResponsivenessTests(unittest.IsolatedAsyncioTestCase):
    async def test_ping_replies_while_tool_is_waiting_for_bodhan(self):
        entered = Event()
        release = Event()
        finished = Event()

        def slow_provider(*args, **kwargs):
            entered.set()
            release.wait(3)
            finished.set()
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "ಮೂರು ಪುಸ್ತಕಗಳು"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
            }), None

        with (
            patch.dict("os.environ", {"BODHAN_TRANSLATE_KEY": "test"}),
            patch("bodhan_mcp.server.post_bodhan", side_effect=slow_provider),
        ):
            async with create_connected_server_and_client_session(mcp) as client:
                pending_tool = asyncio.create_task(client.call_tool(
                    "translate_text", {"text": "Three books", "target_language": "kn"},
                ))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                    await asyncio.wait_for(client.send_ping(), timeout=1)
                    self.assertFalse(finished.is_set(), "The MCP ping waited for the tool to finish.")
                finally:
                    release.set()
                    result = await pending_tool

        self.assertFalse(result.isError)
        self.assertEqual(result.structuredContent["translation"], "ಮೂರು ಪುಸ್ತಕಗಳು")

    async def test_registered_tool_schemas_match_the_sync_functions(self):
        reference = FastMCP("Synchronous tool schemas")
        for tool in (transcribe_audio, synthesize_speech, extract_document_text, translate_text):
            reference.add_tool(tool)
        expected = {tool.name: tool for tool in await reference.list_tools()}

        async with create_connected_server_and_client_session(mcp) as client:
            registered = (await client.list_tools()).tools

        self.assertEqual({tool.name: tool for tool in registered}, expected)
        voices = expected["synthesize_speech"].inputSchema["properties"]["voice"]
        self.assertIn("bn (female Ishita, male Sourav)", voices["description"])
        self.assertIn("hi (female Kavya, Suhani, male Amit)", voices["description"])
        self.assertIn("Deepika", voices["enum"])
