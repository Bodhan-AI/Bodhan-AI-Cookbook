"""
End-to-end test suite for Bodhan Task Agent across all demo tasks.
"""

import os
import shutil
import tempfile
import pytest
from bodhan_agent.agent import TaskAgent


@pytest.fixture
def clean_out_dir():
    temp_dir = tempfile.mkdtemp(prefix="bodhan_test_out_")
    yield temp_dir
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_task_french_impossible(clean_out_dir):
    """Task 6: French video translation -> must fail honestly with clear explanation."""
    agent = TaskAgent(
        task="Translate this video into French",
        files_path="dummy.mp4",
        out_dir=clean_out_dir,
    )
    report = agent.run()

    assert report.status == "IMPOSSIBLE_TASK"
    assert "French" in report.explanation or "french" in report.explanation
    assert os.path.exists(os.path.join(clean_out_dir, "report.json"))
    assert os.path.exists(os.path.join(clean_out_dir, "result.md"))
    assert os.path.exists(os.path.join(clean_out_dir, "trace.jsonl"))


def test_task_tamil_summary(clean_out_dir):
    """Task 1: Scanned chapter Tamil audio summary with back-transcription verification."""
    # Create dummy pdf/image
    dummy_doc = os.path.join(clean_out_dir, "chapter6.pdf")
    with open(dummy_doc, "w") as f:
        f.write("%PDF-1.4 dummy")

    agent = TaskAgent(
        task="Make a two-minute Tamil audio summary of this chapter",
        files_path=clean_out_dir,
        out_dir=clean_out_dir,
    )
    report = agent.run()

    assert report.status == "COMPLETED"
    assert report.total_steps >= 3
    # Check trace was recorded
    assert os.path.exists(os.path.join(clean_out_dir, "trace.jsonl"))
    # Check verification occurred
    assert len(report.verifications) > 0


def test_task_hindi_subtitles(clean_out_dir):
    """Task 2: Hindi voice note transcription & English subtitles."""
    dummy_wav = os.path.join(clean_out_dir, "voicenote.wav")
    with open(dummy_wav, "wb") as f:
        f.write(b"RIFF dummy wav data")

    agent = TaskAgent(
        task="Transcribe this Hindi voice note and give me English subtitles",
        files_path=clean_out_dir,
        out_dir=clean_out_dir,
    )
    report = agent.run()

    assert report.status == "COMPLETED"
    assert report.total_steps >= 2


def test_task_marathi_worksheet(clean_out_dir):
    """Task 3: Translate worksheet into Marathi preserving table."""
    agent = TaskAgent(
        task="Translate this worksheet into Marathi and keep the table",
        files_path=clean_out_dir,
        out_dir=clean_out_dir,
    )
    report = agent.run()

    assert report.status == "COMPLETED"


def test_task_bengali_tts(clean_out_dir):
    """Task 5: Read paragraph aloud in Bengali with female voice."""
    agent = TaskAgent(
        task="Read this paragraph aloud in Bengali with a female voice",
        files_path=clean_out_dir,
        out_dir=clean_out_dir,
    )
    report = agent.run()

    assert report.status == "COMPLETED"
    assert any("speech_bn" in a for a in report.artifacts)


def test_budget_enforcement(clean_out_dir):
    """Verify that exceeding budget stops cleanly with BUDGET_EXCEEDED."""
    agent = TaskAgent(
        task="Make a two-minute Tamil audio summary of this chapter",
        files_path=clean_out_dir,
        out_dir=clean_out_dir,
        max_steps=1,  # Set budget to 1 step
    )
    report = agent.run()

    assert report.status == "BUDGET_EXCEEDED"
    assert report.total_steps == 1
    assert os.path.exists(os.path.join(clean_out_dir, "report.json"))
