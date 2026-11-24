"""End-to-end task execution tests with mocked Bodhan API endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch
import pytest
from bodhan_agent.orchestrator import AgentOrchestrator
from bodhan_mcp.schemas import (
    ExtractTextFromDocumentOutput,
    SynthesizeSpeechOutput,
    TranscribeAudioOutput,
    TranslateTextOutput,
)


@pytest.fixture
def mock_bodhan_tools():
    """Mock the four core model operations to prevent actual API billing during test runs."""
    with patch("bodhan_agent.executor.transcribe_audio") as m_transcribe, \
         patch("bodhan_agent.executor.translate_text") as m_translate, \
         patch("bodhan_agent.executor.synthesize_speech") as m_speak, \
         patch("bodhan_agent.executor.extract_text_from_document") as m_ocr:

        m_transcribe.return_value = TranscribeAudioOutput(
            success=True,
            text="नमस्ते, आज हम विज्ञान का अध्ययन करेंगे।",
            language="hi",
            duration_seconds=5.0,
            chunks=[],
            estimated_cost_inr=0.05,
        )

        m_translate.return_value = TranslateTextOutput(
            success=True,
            translation="Hello, today we will study science.",
            target_language="en",
            script="native",
            source_char_count=35,
            output_char_count=35,
            estimated_cost_inr=0.01,
        )

        m_speak.return_value = SynthesizeSpeechOutput(
            success=True,
            audio_file_path="outputs/mock_speech.wav",
            duration_seconds=3.0,
            language="hi",
            voice="Kavya",
            estimated_cost_inr=0.09,
        )

        m_ocr.return_value = ExtractTextFromDocumentOutput(
            success=True,
            text="# Chapter 1\n\nPhotosynthesis in plants.",
            total_pages=1,
            successful_pages=1,
            failed_pages=0,
            pages=[],
            estimated_cost_inr=0.30,
        )

        yield {
            "transcribe": m_transcribe,
            "translate": m_translate,
            "speak": m_speak,
            "ocr": m_ocr,
        }


def test_agent_e2e_audio_transcribe_and_translate(mock_bodhan_tools, tmp_path: Path):
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"DUMMY_AUDIO_DATA_FOR_TESTING")

    orch = AgentOrchestrator(out_dir=tmp_path / "out", max_steps=10, max_cost_inr=5.0)
    res = orch.run(
        task_prompt="Transcribe this audio file and translate it into English",
        files_path=str(audio_file),
    )

    assert res["status"] in {"completed", "partial"}
    assert (tmp_path / "out" / "trace.jsonl").exists()
    assert (tmp_path / "out" / "report.json").exists()
    assert (tmp_path / "out" / "result.md").exists()

    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    assert report["task_description"] == "Transcribe this audio file and translate it into English"
    assert report["completed_steps"] >= 1


def test_agent_e2e_impossible_language_fails_honestly(tmp_path: Path):
    orch = AgentOrchestrator(out_dir=tmp_path / "out_french", max_steps=10, max_cost_inr=5.0)
    res = orch.run(
        task_prompt="Translate this notice into French",
    )

    # French is unsupported, so the agent must report failed honestly
    assert res["status"] in {"failed", "partial"}
