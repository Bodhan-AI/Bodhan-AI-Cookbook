"""Tests for Model Context Protocol (MCP) JSON-RPC 2.0 communication."""

import unittest
from bodhan_mcp.server import MCPServer


class TestMCPProtocol(unittest.TestCase):
    def test_mcp_initialize(self):
        server = MCPServer()
        req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        }
        resp = server.handle_request(req)
        self.assertEqual(resp["id"], 1)
        self.assertEqual(resp["result"]["serverInfo"]["name"], "bodhan-mcp-server")
        self.assertIn("tools", resp["result"]["capabilities"])

    def test_mcp_tools_list(self):
        server = MCPServer()
        req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        resp = server.handle_request(req)
        self.assertEqual(resp["id"], 2)
        tools = resp["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        self.assertIn("bodhan_transcribe", tool_names)
        self.assertIn("bodhan_translate", tool_names)
        self.assertIn("bodhan_speak", tool_names)
        self.assertIn("bodhan_ocr", tool_names)
        self.assertIn("bodhan_inspect_media", tool_names)

    def test_mcp_tools_call_error_handling(self):
        server = MCPServer()
        req = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "bodhan_ocr",
                "arguments": {"file_path": "non_existent_file_xyz.png"},
            },
        }
        resp = server.handle_request(req)
        self.assertEqual(resp["id"], 3)
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("File not found", resp["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
