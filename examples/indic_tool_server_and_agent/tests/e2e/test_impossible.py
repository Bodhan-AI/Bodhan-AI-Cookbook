"""End-to-end tests for impossible tasks and honest refusal behavior."""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from bodhan_agent import loop
from bodhan_agent.state import TaskStatus
from tests.conftest import make_wav


@pytest.mark.asyncio
async def test_impossible_unsupported_language_french(tmp_path: Path):
    """Refuses task requesting translation to French with honest explanation."""
    in_dir = tmp_path / "inputs"
    in_dir.mkdir()
    video_audio = in_dir / "video_audio.wav"
    video_audio.write_bytes(make_wav(2.0))

    out_dir = tmp_path / "out"

    state = await loop.run(
        task="Translate this video into French with French subtitles",
        files_dir=str(in_dir),
        out_dir=str(out_dir),
    )

    # 1. Status is IMPOSSIBLE
    assert state.status == TaskStatus.IMPOSSIBLE
    assert "french" in state.stop_reason.lower() or "not supported" in state.stop_reason.lower()

    # 2. No fabricated artifacts
    artifacts = list((out_dir / "artifacts").glob("*"))
    assert len(artifacts) == 0

    # 3. Cost is zero (stopped before any model calls)
    assert state.spent_inr == 0.0

    # 4. result.md clearly explains the limitation
    result_md = (out_dir / "result.md").read_text(encoding="utf-8")
    assert "French" in result_md or "french" in result_md
    assert "impossible" in result_md.lower() or "not supported" in result_md.lower()

    # 5. report.json is valid
    report_json = out_dir / "report.json"
    assert report_json.exists()
    report_data = json.loads(report_json.read_text(encoding="utf-8"))
    assert report_data["status"] == "impossible"


@pytest.mark.asyncio
async def test_impossible_unsupported_language_japanese(tmp_path: Path):
    """Refuses task requesting translation to Japanese."""
    in_dir = tmp_path / "inputs"
    in_dir.mkdir()
    sample_txt = in_dir / "doc.txt"
    sample_txt.write_text("नमस्ते", encoding="utf-8")

    out_dir = tmp_path / "out"

    state = await loop.run(
        task="Translate this document to Japanese",
        files_dir=str(in_dir),
        out_dir=str(out_dir),
    )

    assert state.status == TaskStatus.IMPOSSIBLE
    assert "japanese" in state.stop_reason.lower() or "not supported" in state.stop_reason.lower()
    assert state.spent_inr == 0.0


@pytest.mark.asyncio
async def test_impossible_missing_or_empty_inputs(tmp_path: Path):
    """Empty input directory with no files to process is detected as impossible."""
    empty_dir = tmp_path / "empty_inputs"
    empty_dir.mkdir()

    out_dir = tmp_path / "out"

    state = await loop.run(
        task="Transcribe the speech in this folder",
        files_dir=str(empty_dir),
        out_dir=str(out_dir),
    )

    assert state.status == TaskStatus.IMPOSSIBLE
    assert "no input files" in state.stop_reason.lower()
    assert state.spent_inr == 0.0
