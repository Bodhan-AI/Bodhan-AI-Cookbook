import asyncio

from bodhan_agent.runner import TaskAgent


def test_missing_input_writes_blocked_outputs(tmp_path):
    agent = TaskAgent(
        task="Summarize this chapter",
        files=str(tmp_path / "missing"),
        out=str(tmp_path / "out"),
        llm=None,
    )

    report = asyncio.run(agent.run_async())

    assert report["status"] == "blocked"
    assert (tmp_path / "out" / "result.md").is_file()
    assert (tmp_path / "out" / "report.json").is_file()
    assert (tmp_path / "out" / "trace.jsonl").is_file()
