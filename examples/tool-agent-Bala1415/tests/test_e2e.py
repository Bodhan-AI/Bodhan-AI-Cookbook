"""End-to-end test — runs a multi-step task (requires API keys)."""

import os
import pytest
from pathlib import Path


@pytest.mark.e2e
@pytest.mark.skipif(
    not os.getenv("BODHAN_TRANSCRIBE_KEY"),
    reason="Bodhan API keys not set"
)
class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_text_translation_task(self, tmp_path):
        """Test a simple text translation task end-to-end."""
        from bodhan_agent.agent import Agent

        # Create a simple text input
        input_dir = tmp_path / "inputs"
        input_dir.mkdir()
        (input_dir / "greeting.txt").write_text(
            "Hello, good morning! How are you today?",
            encoding="utf-8",
        )

        out_dir = tmp_path / "out"
        agent = Agent(
            task="Translate this English text to Hindi",
            files_dir=str(input_dir),
            output_dir=str(out_dir),
            max_steps=10,
            max_cost_inr=2.0,
            timeout_seconds=60,
        )

        result = await agent.run()

        # Verify output structure
        assert result["status"] in ("completed", "partial")
        assert (out_dir / "result.md").exists()
        assert (out_dir / "trace.jsonl").exists()
        assert (out_dir / "report.json").exists()
        assert (out_dir / "artifacts").is_dir()
