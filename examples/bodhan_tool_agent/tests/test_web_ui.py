#!/usr/bin/env python3
"""Tests for bodhan_agent web UI."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from bodhan_agent.web_ui import (
    WebUIHandler,
    _parse_trace,
    _parse_report,
    _resolve_safe,
    PRESETS,
)


class MockRequest:
    def __init__(self, method, path, body=b"", headers=None):
        self.method = method
        self.path = path
        self.body = body
        self.headers = headers or {}


class MockResponse:
    def __init__(self):
        self.status = 200
        self.headers = {}
        self.body = b""

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.headers[key] = value

    def end_headers(self):
        pass

    def write(self, data):
        self.body += data if isinstance(data, bytes) else data.encode()


@pytest.fixture
def handler():
    """Create a handler instance with mocked I/O."""
    h = WebUIHandler.__new__(WebUIHandler)
    h.headers = {}
    h.rfile = None
    h.wfile = None
    h.request = None
    h.client_address = ("127.0.0.1", 12345)
    h.server = None
    h.responses = {}
    return h


def test_presets_structure():
    """PRESETS should have all required fields and demo_inputs paths."""
    assert len(PRESETS) == 6
    for p in PRESETS:
        assert "id" in p and p["id"]
        assert "name" in p and p["name"]
        assert "task" in p and p["task"]
        assert "files" in p and isinstance(p["files"], list) and len(p["files"]) > 0
        for f in p["files"]:
            assert f.startswith("demo_inputs/")


def test_resolve_safe_allows_outputs():
    """_resolve_safe should allow paths under OUTPUTS_DIR."""
    from bodhan_agent.web_ui import OUTPUTS_DIR
    test_file = OUTPUTS_DIR / "test.txt"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("hi")
    try:
        resolved = _resolve_safe(test_file)
        assert resolved is not None
        assert resolved == test_file.resolve()
    finally:
        test_file.unlink(missing_ok=True)


def test_resolve_safe_rejects_traversal():
    """_resolve_safe should reject paths outside OUTPUTS_DIR."""
    assert _resolve_safe(Path("/etc/passwd")) is None
    assert _resolve_safe(Path("../../etc/passwd")) is None
    assert _resolve_safe(Path("C:/Windows/System32")) is None


def test_parse_trace(tmp_path):
    """_parse_trace should parse JSONL lines correctly."""
    trace_file = tmp_path / "trace.jsonl"
    trace_file.write_text(
        '{"step": 1, "tool": "inspect_media", "status": "started"}\n'
        '{"step": 1, "tool": "inspect_media", "status": "completed", "latency_ms": 10.5}\n'
        'invalid line\n'
        '{"step": 2, "tool": "translate_text", "status": "started"}\n',
        encoding="utf-8",
    )
    steps = _parse_trace(trace_file)
    assert len(steps) == 3
    assert steps[0]["step"] == 1
    assert steps[0]["tool"] == "inspect_media"
    assert steps[1]["latency_ms"] == 10.5
    assert steps[2]["step"] == 2


def test_parse_report(tmp_path):
    """_parse_report should parse valid JSON."""
    report_file = tmp_path / "report.json"
    report_file.write_text(
        json.dumps({"status": "completed", "total_cost_inr": 1.23}),
        encoding="utf-8",
    )
    report = _parse_report(report_file)
    assert report is not None
    assert report["status"] == "completed"
    assert report["total_cost_inr"] == 1.23

    # Missing file returns None
    assert _parse_report(tmp_path / "missing.json") is None


def test_handler_do_get_root(handler):
    """GET / should serve HTML."""
    with patch.object(handler, "send_response") as mock_send, \
         patch.object(handler, "send_header") as mock_header, \
         patch.object(handler, "end_headers"), \
         patch.object(handler, "wfile") as mock_wfile:

        handler.path = "/"
        handler.do_GET()

        mock_send.assert_called_with(200)
        mock_header.assert_any_call("Content-Type", "text/html; charset=utf-8")
        assert mock_wfile.write.called
        html = mock_wfile.write.call_args[0][0]
        assert b"bodhan_agent" in html
        assert b"Live Trace" in html


def test_handler_do_get_presets(handler):
    """GET /api/presets should return JSON with presets."""
    with patch.object(handler, "send_response") as mock_send, \
         patch.object(handler, "send_header") as mock_header, \
         patch.object(handler, "end_headers"), \
         patch.object(handler, "wfile") as mock_wfile:

        handler.path = "/api/presets"
        handler.do_GET()

        mock_send.assert_called_with(200)
        mock_header.assert_any_call("Content-Type", "application/json; charset=utf-8")
        assert mock_wfile.write.called
        data = json.loads(mock_wfile.write.call_args[0][0].decode())
        assert "presets" in data
        assert len(data["presets"]) == 6


def test_handler_do_get_status_missing_run_id(handler):
    """GET /api/status without run_id returns 400."""
    with patch.object(handler, "send_response") as mock_send, \
         patch.object(handler, "send_header"), \
         patch.object(handler, "end_headers"), \
         patch.object(handler, "wfile") as mock_wfile:

        handler.path = "/api/status"
        handler.do_GET()

        mock_send.assert_called_with(400)
        data = json.loads(mock_wfile.write.call_args[0][0].decode())
        assert "error" in data


def test_handler_do_get_status_not_found(handler):
    """GET /api/status for non-existent run returns 404."""
    with patch.object(handler, "send_response") as mock_send, \
         patch.object(handler, "send_header"), \
         patch.object(handler, "end_headers"), \
         patch.object(handler, "wfile") as mock_wfile:

        handler.path = "/api/status?run_id=nonexistent"
        handler.do_GET()

        mock_send.assert_called_with(404)
        data = json.loads(mock_wfile.write.call_args[0][0].decode())
        assert "error" in data


def test_handler_do_post_run_valid(handler):
    """POST /api/run with valid data starts a run."""
    with patch.object(handler, "send_response") as mock_send, \
         patch.object(handler, "send_header"), \
         patch.object(handler, "end_headers"), \
         patch.object(handler, "wfile") as mock_wfile, \
         patch("bodhan_agent.web_ui._run_agent_background") as mock_run:

        handler.path = "/api/run"
        handler.headers = {"Content-Length": "80"}
        body = json.dumps({
            "task": "Test task",
            "files": ["demo_inputs/Task1_8pages.pdf"],
            "max_steps": 10,
            "max_cost_inr": 5.0,
        }).encode()
        handler.rfile = type("obj", (object,), {"read": lambda self, n: body})()

        handler.do_POST()

        mock_send.assert_called_with(200)
        data = json.loads(mock_wfile.write.call_args[0][0].decode())
        assert "run_id" in data
        assert data["status"] == "started"
        mock_run.assert_called_once()


def test_handler_do_post_run_missing_task(handler):
    """POST /api/run without task returns 400."""
    with patch.object(handler, "send_response") as mock_send, \
         patch.object(handler, "send_header"), \
         patch.object(handler, "end_headers"), \
         patch.object(handler, "wfile") as mock_wfile:

        handler.path = "/api/run"
        handler.headers = {"Content-Length": "30"}
        body = json.dumps({"files": ["demo_inputs/Task1_8pages.pdf"]}).encode()
        handler.rfile = type("obj", (object,), {"read": lambda self, n: body})()

        handler.do_POST()

        mock_send.assert_called_with(400)
        data = json.loads(mock_wfile.write.call_args[0][0].decode())
        assert "error" in data


def test_handler_do_post_run_missing_files(handler):
    """POST /api/run without files returns 400."""
    with patch.object(handler, "send_response") as mock_send, \
         patch.object(handler, "send_header"), \
         patch.object(handler, "end_headers"), \
         patch.object(handler, "wfile") as mock_wfile:

        handler.path = "/api/run"
        handler.headers = {"Content-Length": "20"}
        body = json.dumps({"task": "Test"}).encode()
        handler.rfile = type("obj", (object,), {"read": lambda self, n: body})()

        handler.do_POST()

        mock_send.assert_called_with(400)
        data = json.loads(mock_wfile.write.call_args[0][0].decode())
        assert "error" in data


def test_artifact_path_traversal_rejection(tmp_path):
    """Artifact serving should reject paths outside OUTPUTS_DIR."""
    # Create a test file outside OUTPUTS_DIR
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")

    # The handler's _serve_artifact uses _resolve_safe which rejects outside paths
    resolved = _resolve_safe(outside)
    assert resolved is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
