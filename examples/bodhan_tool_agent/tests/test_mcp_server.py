from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


from bodhan_agent.eval_suite import build_simulated_bodhan_transport
from bodhan_mcp.client import BodhanClient
from bodhan_mcp.config import BodhanConfig
from bodhan_mcp.server import BodhanMCPServer
from bodhan_mcp.tools import BodhanToolSuite


def test_mcp_server_jsonrpc_protocol_and_bad_inputs(tmp_path: Path) -> None:
    cfg = BodhanConfig.from_env(cache_dir_override=tmp_path / "cache")
    client = BodhanClient(config=cfg, http_transport=build_simulated_bodhan_transport())
    suite = BodhanToolSuite(client=client, artifacts_dir=tmp_path / "artifacts")
    server = BodhanMCPServer(tool_suite=suite)

    init_resp = server.handle_jsonrpc_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}
    )
    assert init_resp is not None
    assert init_resp["result"]["serverInfo"]["name"] == "bodhan-mcp"

    tools_resp = server.handle_jsonrpc_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert tools_resp is not None
    tools = tools_resp["result"]["tools"]
    tool_names = {t["name"] for t in tools}
    assert {
        "inspect_media_and_files",
        "extract_document_text",
        "transcribe_audio",
        "translate_text",
        "synthesize_speech",
        "verify_translation_quality",
        "verify_audio_quality",
    }.issubset(tool_names)

    res_list = server.handle_jsonrpc_message({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
    assert res_list is not None and len(res_list["result"]["resources"]) == 3

    voices_res = server.handle_jsonrpc_message(
        {"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": "bodhan://capabilities/voices"}}
    )
    assert voices_res is not None
    voices_data = json.loads(voices_res["result"]["contents"][0]["text"])
    assert voices_data["voice_library"]["bn"]["female"] == "Ishita"

    prompts_list = server.handle_jsonrpc_message({"jsonrpc": "2.0", "id": 5, "method": "prompts/list"})
    assert prompts_list is not None and len(prompts_list["result"]["prompts"]) == 2

    bad_tool = server.handle_jsonrpc_message(
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "nonexistent_tool", "arguments": {}}}
    )
    assert bad_tool is not None and bad_tool["result"]["isError"] is True
    assert bad_tool["result"]["structuredContent"]["error_code"] == "UNKNOWN_TOOL"

    missing_arg = server.handle_jsonrpc_message(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "transcribe_audio", "arguments": {}}}
    )
    assert missing_arg is not None and missing_arg["result"]["isError"] is True
    assert missing_arg["result"]["structuredContent"]["error_code"] == "INVALID_TOOL_ARGUMENTS"

    missing_file = server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {"name": "extract_document_text", "arguments": {"file_path": "/nonexistent/fake_doc.pdf"}},
        }
    )
    assert missing_file is not None and missing_file["result"]["isError"] is True
    assert missing_file["result"]["structuredContent"]["error_code"] == "FILE_NOT_FOUND"

    unsupported_lang = server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "translate_text", "arguments": {"target_language": "fr", "text": "Hello world"}},
        }
    )
    assert unsupported_lang is not None and unsupported_lang["result"]["isError"] is True
    assert unsupported_lang["result"]["structuredContent"]["error_code"] == "UNSUPPORTED_LANGUAGE"

    valid_call = server.handle_jsonrpc_message(
        {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {
                "name": "translate_text",
                "arguments": {"target_language": "ta", "text": "Lines and angles summary"},
            },
        }
    )
    assert valid_call is not None and valid_call["result"]["isError"] is False
    assert valid_call["result"]["structuredContent"]["status"] == "ok"
    assert Path(valid_call["result"]["structuredContent"]["output_path"]).exists()


def test_mcp_stdio_subprocess_standalone(tmp_path: Path) -> None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "bodhan_mcp", "--artifacts-dir", str(tmp_path / "stdio_arts")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdin is not None and proc.stdout is not None
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "inspect_media_and_files", "arguments": {"path": str(tmp_path)}},
            },
        ]
        for req in requests:
            proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()
        proc.stdin.close()

        lines = [ln.strip() for ln in proc.stdout.read().splitlines() if ln.strip()]
        assert len(lines) == 3
        r1 = json.loads(lines[0])
        r2 = json.loads(lines[1])
        r3 = json.loads(lines[2])
        assert r1["id"] == 1 and "serverInfo" in r1["result"]
        assert r2["id"] == 2 and len(r2["result"]["tools"]) == 7
        assert r3["id"] == 3 and r3["result"]["isError"] is True
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_mcp_http_transport_with_token_auth(tmp_path: Path) -> None:
    server = BodhanMCPServer(tool_suite=BodhanToolSuite(artifacts_dir=tmp_path / "http_arts"))
    secret = "test-secret-token-123"

    code_unauth, _, body_unauth = server.handle_http_request(
        method="GET",
        path="/health",
        headers={},
        expected_token=secret,
    )
    assert code_unauth == 401
    assert b"Unauthorized" in body_unauth

    code_health, _, body_health = server.handle_http_request(
        method="GET",
        path="/health",
        headers={"Authorization": f"Bearer {secret}"},
        expected_token=secret,
    )
    assert code_health == 200
    assert json.loads(body_health.decode("utf-8"))["transport"] == "streamable-http"

    code_post, _, body_post = server.handle_http_request(
        method="POST",
        path="/mcp",
        headers={"Authorization": f"Bearer {secret}"},
        body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        expected_token=secret,
    )
    assert code_post == 200
    parsed = json.loads(body_post.decode("utf-8"))
    assert len(parsed["result"]["tools"]) == 7
