"""End-to-end multi-step task execution tests using fully mocked clients."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from bodhan_agent import loop
from bodhan_agent.planner import TaskInterpretation
from bodhan_agent.state import TaskStatus
from tests.conftest import make_wav


@pytest.mark.asyncio
async def test_e2e_transcribe_translate_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Full end-to-end multi-step run: transcribe -> translate -> verify -> write outputs."""
    cache_dir = tmp_path / "test_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("BODHAN_CACHE_DIR", str(cache_dir))
    monkeypatch.setattr("bodhan_mcp.core.cache._CACHE_DIR", cache_dir)

    in_dir = tmp_path / "inputs"
    in_dir.mkdir()
    audio_file = in_dir / "kannada_audio.wav"
    audio_file.write_bytes(make_wav(3.0))

    out_dir = tmp_path / "out"

    # Mock planner interpretation and 2-step plan
    interp = TaskInterpretation(
        goal="Transcribe Kannada audio and translate to English",
        deliverables=["transcript.txt", "translation.txt"],
        languages=["kn", "en"],
        assumptions=["Audio language is Kannada"],
        feasible=True,
    )

    steps = [
        loop.PlanStep(
            id="step_stt",
            tool="transcribe_audio",
            args={"file_path": str(audio_file), "language": "kn"},
            purpose="Transcribe Kannada audio",
            expected_output="Kannada transcript text",
        ),
        loop.PlanStep(
            id="step_trans",
            tool="translate_text",
            args={"text": "${step_stt.full_text}", "target_language": "en", "source_language": "kn"},
            depends_on=["step_stt"],
            purpose="Translate Kannada to English",
            expected_output="English translation text",
        ),
    ]

    mock_stt_client = AsyncMock()
    mock_stt_client.transcribe_chunk.return_value = {
        "text": "ನಮಸ್ಕಾರ, ಇದು ಕನ್ನಡ ಧ್ವನಿ ಮುದ್ರಣ",
        "language": "kn",
    }

    mock_trans_client = AsyncMock()
    mock_trans_client.translate_text.return_value = {
        "translated_text": "Hello, this is a Kannada audio recording",
        "output_tokens": 8,
    }

    with patch("bodhan_agent.planner.interpret_task", return_value=interp), \
         patch("bodhan_agent.planner.make_plan", return_value=steps), \
         patch("bodhan_mcp.tools.stt.TranscribeClient", return_value=mock_stt_client), \
         patch("bodhan_mcp.tools.translate.TranslateClient", return_value=mock_trans_client):

        state = await loop.run(
            task="Transcribe this Kannada audio and translate it to English",
            files_dir=str(in_dir),
            out_dir=str(out_dir),
            max_steps=10,
            max_cost_inr=5.0,
        )

    # 1. State assertions
    assert state.status == TaskStatus.COMPLETED
    assert state.spent_inr >= 0.0
    assert state.steps_used == 2

    # 2. Output files exist
    result_md = out_dir / "result.md"
    report_json = out_dir / "report.json"
    trace_jsonl = out_dir / "trace.jsonl"

    assert result_md.exists(), "result.md missing"
    assert report_json.exists(), "report.json missing"
    assert trace_jsonl.exists(), "trace.jsonl missing"

    # 3. Verify report.json content
    report_data = json.loads(report_json.read_text(encoding="utf-8"))
    assert report_data["status"] == "completed"
    assert report_data["goal"] == "Transcribe Kannada audio and translate to English"
    assert "total_cost_inr" in report_data
    assert "steps_used" in report_data
    assert "artifacts" in report_data

    # 4. Verify trace.jsonl format and events
    trace_lines = [
        json.loads(line) for line in trace_jsonl.read_text(encoding="utf-8").strip().split("\n")
        if line.strip()
    ]
    event_types = {e.get("event") for e in trace_lines}
    assert "plan" in event_types
    assert "step_start" in event_types
    assert "step_complete" in event_types or "step_end" in event_types

    # 5. Verify result.md contains human-readable summary
    md_content = result_md.read_text(encoding="utf-8")
    assert "Transcribe Kannada audio and translate to English" in md_content
    assert "Status" in md_content
