from __future__ import annotations

from bodhan_agent.errors import ToolError
from bodhan_agent.settings import Settings
from bodhan_mcp import __main__ as mcp_server


def test_mcp_run_calls_executor_with_required_model(monkeypatch, tmp_path):
    settings = Settings("transcribe", "translate", "speak", "ocr", "", "", "")
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls, **kwargs: (kwargs, settings)[1]))
    monkeypatch.setenv("BODHAN_MCP_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    seen = {}

    class FakeExecutor:
        def __init__(self, received_settings, artifacts_dir):
            seen["settings"] = received_settings
            seen["artifacts_dir"] = artifacts_dir

        def call(self, name, arguments, *, max_cost_inr, spent_inr):
            seen.update({"name": name, "arguments": arguments, "max_cost": max_cost_inr, "spent": spent_inr})
            return {"text": "ok"}

    monkeypatch.setattr(mcp_server, "ToolExecutor", FakeExecutor)
    result = mcp_server._run("transcribe_audio", {"file_ref": "sample.wav"}, "transcribe")

    assert result == {"text": "ok"}
    assert seen["settings"] is settings
    assert seen["name"] == "transcribe_audio"
    assert seen["arguments"] == {"file_ref": "sample.wav"}
    assert seen["max_cost"] == float("inf")
    assert seen["spent"] == 0.0


def test_mcp_run_returns_structured_tool_error(monkeypatch):
    settings = Settings("transcribe", "translate", "speak", "ocr", "", "", "")
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls, **kwargs: settings))

    class FailingExecutor:
        def __init__(self, *args):
            pass

        def call(self, *args, **kwargs):
            raise ToolError("bad input", "invalid_input")

    monkeypatch.setattr(mcp_server, "ToolExecutor", FailingExecutor)
    assert mcp_server._run("translate_text", {}, "translate") == {
        "error": {"code": "invalid_input", "message": "bad input"}
    }


def test_mcp_main_uses_stdio_transport(monkeypatch):
    seen = {}
    monkeypatch.setattr(mcp_server.server, "run", lambda **kwargs: seen.update(kwargs))
    mcp_server.main()
    assert seen == {"transport": "stdio"}
