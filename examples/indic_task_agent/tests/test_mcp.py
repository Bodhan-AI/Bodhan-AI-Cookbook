import unittest

from indic_task_agent.mcp_server import mcp


class McpServerTests(unittest.TestCase):
    def test_server_registers_exactly_the_four_bodhan_tools(self):
        tools = mcp._tool_manager.list_tools()
        self.assertEqual({tool.name for tool in tools}, {"indic_ocr", "indic_asr", "indic_translate", "indic_tts"})


if __name__ == "__main__":
    unittest.main()
