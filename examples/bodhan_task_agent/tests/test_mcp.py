from bodhan_mcp.__main__ import handle
from bodhan_shared.tools import ToolHub


def test_mcp_standalone_handshake_and_schemas(tmp_path):
    hub = ToolHub(tmp_path)
    initialized = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2025-06-18"}}, hub)
    assert initialized["result"]["capabilities"]["tools"] == {"listChanged": False}
    listed = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, hub)
    assert len(listed["result"]["tools"]) >= 8
    assert all(t["inputSchema"]["type"] == "object" for t in listed["result"]["tools"])
    invalid = handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                      "params": {"name": "ocr_document", "arguments": {}}}, hub)
    assert invalid["result"]["isError"]
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, hub) is None
