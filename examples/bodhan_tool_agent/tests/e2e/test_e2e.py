"""
End-to-end test for bodhan_agent loop.
Executes an end-to-end multi-step task with synthetic inputs and mocks.
"""
import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bodhan_agent.loop import run_task


def test_end_to_end_agent_execution(tmp_path):
    async def _test():
        # 1. Create a dummy input text file
        input_file = tmp_path / "sample.txt"
        input_file.write_text("Hello world, this is a test worksheet.", encoding="utf-8")

        out_dir = tmp_path / "out"

        # 2. Run the task agent
        await run_task(
            task="Translate this worksheet into Hindi",
            files=input_file,
            out_dir=out_dir,
            max_steps=5,
            max_cost_inr=10.0,
        )

        # 3. Verify output files layout
        report_file = out_dir / "report.json"
        result_file = out_dir / "result.md"
        trace_file = out_dir / "trace.jsonl"

        assert report_file.exists(), "report.json must be generated"
        assert result_file.exists(), "result.md must be generated"
        assert trace_file.exists(), "trace.jsonl must be generated"

        report_data = json.loads(report_file.read_text(encoding="utf-8"))
        assert "status" in report_data
        assert report_data["status"] in ["completed", "partial", "refused", "running"]

        trace_lines = trace_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(trace_lines) >= 1, "trace.jsonl must contain step events"

    asyncio.run(_test())
