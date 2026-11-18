"""Integration tests for agent loop scenarios using mocked clients and planner."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from bodhan_agent import loop, planner
from bodhan_agent.planner import PlanSpec, StepSpec, TaskInterpretation
from bodhan_agent.state import TaskState, TaskStatus
from tests.conftest import make_wav


@pytest.mark.asyncio
async def test_scenario_single_step_transcribe(tmp_path: Path):
    """Test single-step transcribe task end-to-end on mocks."""
    in_dir = tmp_path / "inputs"
    in_dir.mkdir()
    audio_file = in_dir / "hindi_speech.wav"
    audio_file.write_bytes(make_wav(2.0))

    out_dir = tmp_path / "out"

    # Script planner interpretation & plan
    interp = TaskInterpretation(
        goal="Transcribe Hindi audio file",
        deliverables=["transcript"],
        languages=["hi"],
        assumptions=[],
        feasible=True,
    )
    plan_steps = [
        PlanSpec(
            steps=[
                StepSpec(
                    id="step_1",
                    tool="transcribe_audio",
                    args={"file_path": str(audio_file), "language": "hi"},
                    purpose="Transcribe the speech",
                    expected_output="transcript text",
                )
            ],
            rationale="Single step transcribe",
        )
    ]

    mock_client = AsyncMock()
    mock_client.transcribe_chunk.return_value = {
        "text": "नमस्ते यह परीक्षण प्रतिलेखन है",
        "language": "hi",
    }

    with patch("bodhan_agent.planner.interpret_task", return_value=interp), \
         patch("bodhan_agent.planner.make_plan", return_value=[
             loop.PlanStep(
                 id="step_1",
                 tool="transcribe_audio",
                 args={"file_path": str(audio_file), "language": "hi"},
                 purpose="Transcribe",
             )
         ]), \
         patch("bodhan_mcp.tools.stt.TranscribeClient", return_value=mock_client):

        state = await loop.run(
            task="Transcribe this Hindi speech",
            files_dir=str(in_dir),
            out_dir=str(out_dir),
        )

    assert state.status == TaskStatus.COMPLETED
    assert (out_dir / "result.md").exists()
    assert (out_dir / "report.json").exists()
    assert (out_dir / "trace.jsonl").exists()

    result_text = (out_dir / "result.md").read_text(encoding="utf-8")
    assert "Transcribe Hindi audio file" in result_text or "goal" in result_text.lower()


@pytest.mark.asyncio
async def test_scenario_transcribe_and_translate(tmp_path: Path):
    """Test 2-step chained pipeline: transcribe then translate."""
    in_dir = tmp_path / "inputs"
    in_dir.mkdir()
    audio_file = in_dir / "speech.wav"
    audio_file.write_bytes(make_wav(2.0))

    out_dir = tmp_path / "out"

    interp = TaskInterpretation(
        goal="Transcribe Hindi and translate to English",
        deliverables=["transcript", "translation"],
        languages=["hi", "en"],
        assumptions=[],
        feasible=True,
    )

    steps = [
        loop.PlanStep(
            id="s1",
            tool="transcribe_audio",
            args={"file_path": str(audio_file), "language": "hi"},
            purpose="Transcribe",
        ),
        loop.PlanStep(
            id="s2",
            tool="translate_text",
            args={"text": "${s1.full_text}", "target_language": "en"},
            depends_on=["s1"],
            purpose="Translate to English",
        ),
    ]

    mock_stt = AsyncMock()
    mock_stt.transcribe_chunk.return_value = {"text": "नमस्ते दुनिया", "language": "hi"}

    mock_trans = AsyncMock()
    mock_trans.translate_text.return_value = {"translated_text": "Hello world", "output_tokens": 2}

    with patch("bodhan_agent.planner.interpret_task", return_value=interp), \
         patch("bodhan_agent.planner.make_plan", return_value=steps), \
         patch("bodhan_mcp.tools.stt.TranscribeClient", return_value=mock_stt), \
         patch("bodhan_mcp.tools.translate.TranslateClient", return_value=mock_trans):

        state = await loop.run(
            task="Transcribe and translate to English",
            files_dir=str(in_dir),
            out_dir=str(out_dir),
        )

    assert state.status == TaskStatus.COMPLETED
    # Verify execution order: s1 must have executed before s2
    s1 = state.get_step("s1")
    s2 = state.get_step("s2")
    assert s1.status == loop.StepStatus.DONE
    assert s2.status == loop.StepStatus.DONE
    assert s2.result["translated_text"] == "Hello world"


@pytest.mark.asyncio
async def test_scenario_impossible_task_french(tmp_path: Path):
    """Task requiring unsupported language (French) is immediately marked IMPOSSIBLE."""
    in_dir = tmp_path / "inputs"
    in_dir.mkdir()
    audio_file = in_dir / "hindi.wav"
    audio_file.write_bytes(make_wav(1.0))

    out_dir = tmp_path / "out"

    state = await loop.run(
        task="Translate this video/audio into French",
        files_dir=str(in_dir),
        out_dir=str(out_dir),
    )

    assert state.status == TaskStatus.IMPOSSIBLE
    assert "french" in state.stop_reason.lower() or "not supported" in state.stop_reason.lower()

    # Verify no fabricated output files
    artifacts = list((out_dir / "artifacts").glob("*"))
    assert len(artifacts) == 0

    result_md = (out_dir / "result.md").read_text(encoding="utf-8")
    assert "French" in result_md or "french" in result_md
    assert "impossible" in result_md.lower() or "not supported" in result_md.lower()


@pytest.mark.asyncio
async def test_scenario_budget_exceeded(tmp_path: Path):
    """Exceeding max_steps stops loop cleanly with BUDGET_EXHAUSTED."""
    in_dir = tmp_path / "inputs"
    in_dir.mkdir()
    audio_file = in_dir / "audio.wav"
    audio_file.write_bytes(make_wav(1.0))

    out_dir = tmp_path / "out"

    interp = TaskInterpretation(
        goal="Process audio",
        deliverables=["output"],
        languages=["hi"],
        feasible=True,
    )

    # 3 sequential steps, but max_steps will be 1
    steps = [
        loop.PlanStep(id="s1", tool="transcribe_audio", args={"file_path": str(audio_file)}),
        loop.PlanStep(id="s2", tool="translate_text", args={"text": "xyz", "target_language": "en"}, depends_on=["s1"]),
    ]

    mock_stt = AsyncMock()
    mock_stt.transcribe_chunk.return_value = {"text": "नमस्ते", "language": "hi"}

    with patch("bodhan_agent.planner.interpret_task", return_value=interp), \
         patch("bodhan_agent.planner.make_plan", return_value=steps), \
         patch("bodhan_mcp.tools.stt.TranscribeClient", return_value=mock_stt):

        state = await loop.run(
            task="Transcribe and translate",
            files_dir=str(in_dir),
            out_dir=str(out_dir),
            max_steps=1,
        )

    assert state.status in {TaskStatus.BUDGET_EXHAUSTED, TaskStatus.PARTIAL}
    assert (out_dir / "report.json").exists()
