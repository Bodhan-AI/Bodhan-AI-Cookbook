import asyncio
import json
from pathlib import Path

from bodhan_agent.agent import TaskAgent, _request_url


def test_openai_compatible_endpoint_url():
    assert _request_url("https://llm.example/v1/") == "https://llm.example/v1/chat/completions"
    assert _request_url("https://llm.example/v1/chat/completions") == "https://llm.example/v1/chat/completions"


def test_agent_runs_mcp_tool_chain_and_writes_assignment_outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("BODHAN_CACHE_BACKEND", "memory")
    monkeypatch.setenv("BODHAN_ARTIFACT_DIR", str(tmp_path / "mcp-artifacts"))
    monkeypatch.setenv("BODHAN_MODEL_CACHE_ARTIFACT_DIR", str(tmp_path / "model-artifacts"))
    input_file = tmp_path / "input.txt"
    input_file.write_text("A short English note.", encoding="utf-8")
    agent = TaskAgent(
        task="Read the supplied note and report its content.",
        input_paths=[input_file],
        output_root=tmp_path / "runs",
        max_steps=5,
        max_cost_inr=1.0,
        tool_timeout_seconds=15,
        llm_timeout_seconds=5,
        model="test-model",
        base_url="http://unused.invalid/v1",
        api_key="test-key",
    )
    responses = [
        {"content": "1. Read the note with get_file. 2. Report its text."},
        {
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "get_file", "arguments": json.dumps({"file_ref": str(input_file)})},
                }
            ],
        },
        {"content": "The note says: A short English note."},
    ]

    async def fake_llm_message(messages, tools=None):
        assert responses, "unexpected additional LLM call"
        return responses.pop(0)

    monkeypatch.setattr(agent, "_llm_message", fake_llm_message)
    run_dir = asyncio.run(agent.run())

    assert agent.status == "completed"
    assert (run_dir / "result.md").is_file()
    assert (run_dir / "report.json").is_file()
    assert (run_dir / "trace.jsonl").is_file()
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert report["steps"] == 1
    assert report["tool_events"][0]["tool"] == "get_file"
    assert "A short English note" in (run_dir / "result.md").read_text(encoding="utf-8")
    assert any(event["type"] == "plan" for event in [json.loads(line) for line in (run_dir / "trace.jsonl").read_text().splitlines()])
