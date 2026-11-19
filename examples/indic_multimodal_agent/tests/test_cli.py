"""Unit tests for the Bodhan Task Agent CLI and progress reporting.
Zero paid API calls: uses mocks or simulated runs to test CLI behavior,
error handling, progress output, and report generation.
"""

import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from bodhan_agent.agent import BodhanTaskAgent
from bodhan_mcp.types import TranscribeOutput, SubtitleSegment
from bodhan_agent.verifier import VerificationResult


@pytest.fixture
def temp_dirs(tmp_path):
    files_dir = tmp_path / "inputs"
    out_dir = tmp_path / "outputs"
    files_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    return files_dir, out_dir


def test_cli_missing_input_directory(tmp_path):
    """Test CLI fails non-zero and writes report.json when files dir does not exist."""
    non_existent = tmp_path / "non_existent_dir"
    out_dir = tmp_path / "out_missing"

    agent = BodhanTaskAgent(
        task="Transcribe and create SRT",
        files_dir=str(non_existent),
        out_dir=str(out_dir),
        max_cost_inr=5.0,
    )
    success = agent.execute()

    assert success is False
    report_file = out_dir / "report.json"
    assert report_file.exists()
    with open(report_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["status"] == "failed"
    assert "INPUT_DIR_NOT_FOUND" in data["error"] or "does not exist" in data["error"]


def test_cli_empty_input_directory(temp_dirs):
    """Test CLI fails non-zero and writes report.json when no media files exist."""
    files_dir, out_dir = temp_dirs

    agent = BodhanTaskAgent(
        task="Transcribe and create SRT",
        files_dir=str(files_dir),
        out_dir=str(out_dir),
        max_cost_inr=5.0,
    )
    success = agent.execute()

    assert success is False
    report_file = out_dir / "report.json"
    assert report_file.exists()
    with open(report_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["status"] == "failed"
    assert "NO_INPUT_FILES" in data["error"]


def test_cli_impossible_task_handling(temp_dirs, capsys):
    """Test CLI handles impossible task (e.g. French translation) with explanation and report."""
    files_dir, out_dir = temp_dirs

    # Create dummy audio file
    dummy_wav = files_dir / "test.wav"
    dummy_wav.write_bytes(b"RIFF" + b"\x00" * 40)

    agent = BodhanTaskAgent(
        task="Translate this Hindi audio note to French subtitles",
        files_dir=str(files_dir),
        out_dir=str(out_dir),
        max_cost_inr=5.0,
    )
    success = agent.execute()

    assert success is True  # Resolved cleanly with honest explanation
    captured = capsys.readouterr()
    assert "IMPOSSIBLE" in captured.out
    assert "unsupported non-Indic language" in captured.out

    report_file = out_dir / "report.json"
    assert report_file.exists()
    with open(report_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["status"] == "impossible"
    assert data["total_cost_inr"] == 0.0


def test_cli_progress_lines_and_summary_mocked(temp_dirs, capsys):
    """Test that CLI prints rich progress lines, verification checks, and summary."""
    files_dir, out_dir = temp_dirs

    # Create dummy audio file
    dummy_mp3 = files_dir / "audio.mp3"
    dummy_mp3.write_bytes(b"\xFF\xFB" + b"\x00" * 500)

    # Mock audio inspector, transcribe, and translate
    mock_audio_info = {"duration": 12.0, "channels": 1, "samplerate": 16000, "format": "MP3"}
    mock_segments = [
        SubtitleSegment(
            index=1,
            start_time="00:00:00,000",
            end_time="00:00:05,000",
            start_seconds=0.0,
            end_seconds=5.0,
            text="नमस्ते, आज का दिन बहुत अच्छा है।",
        ),
        SubtitleSegment(
            index=2,
            start_time="00:00:05,000",
            end_time="00:00:10,000",
            start_seconds=5.0,
            end_seconds=10.0,
            text="हम परियोजना पूरी कर रहे हैं।",
        ),
    ]
    mock_trans_res = TranscribeOutput(
        text="नमस्ते, आज का दिन बहुत अच्छा है। हम परियोजना पूरी कर रहे हैं।",
        duration_seconds=12.0,
        segments=mock_segments,
        cost_inr=0.015,
        cached=False,
    )
    mock_translated_lines = [
        "Hello, today is a very good day.",
        "We are completing the project.",
    ]

    with patch("bodhan_agent.agent.get_audio_info", return_value=mock_audio_info), \
         patch("bodhan_agent.agent.transcribe_audio", return_value=mock_trans_res), \
         patch("bodhan_agent.agent.translate_subtitles_batch", return_value=(mock_translated_lines, 50, False)):

        agent = BodhanTaskAgent(
            task="Transcribe this Hindi voice note and give me English subtitles as an SRT file",
            files_dir=str(files_dir),
            out_dir=str(out_dir),
            max_cost_inr=5.0,
        )
        success = agent.execute()

    assert success is True
    captured = capsys.readouterr()

    # Assert progress lines
    assert "=== Bodhan Task Agent ===" in captured.out
    assert "[Plan] Inspecting input media properties" in captured.out
    assert "[Tool: bodhan_inspect_media] Done in" in captured.out
    assert "[Budget] Pre-flight estimate:" in captured.out
    assert "Calling bodhan_transcribe" in captured.out
    assert "bodhan_transcribe] Done in" in captured.out
    assert "bodhan_detect_language] Done in" in captured.out
    assert "bodhan_translate] Done in" in captured.out
    assert "[Verify] Running verification suite on generated subtitles" in captured.out
    assert "[PASS] srt_validity" in captured.out
    assert "[PASS] script_match" in captured.out
    assert "[PASS] unicode_normalization" in captured.out

    # Assert run summary
    assert "=== Run Summary ===" in captured.out
    assert "Status:        COMPLETED" in captured.out
    assert "Total Cost:    ₹" in captured.out
    assert "Artifacts:" in captured.out
    assert "subtitles_en.srt" in captured.out
    assert "report.json" in captured.out

    # Assert generated files
    assert (out_dir / "report.json").exists()
    assert (out_dir / "trace.jsonl").exists()
    assert (out_dir / "result.md").exists()
    assert (out_dir / "artifacts" / "subtitles_en.srt").exists()

    with open(out_dir / "report.json", "r", encoding="utf-8") as f:
        rep = json.load(f)
    assert rep["status"] == "completed"
    assert rep["total_steps"] >= 4
    assert rep["total_cost_inr"] > 0
    assert len(rep["verification_results"]) >= 4


def test_stage_a_pdf_audio_summary_mocked(temp_dirs, capsys):
    """Test Stage A PDF -> OCR -> Tamil Audio Summary pipeline with mocks (0 paid calls)."""
    files_dir, out_dir = temp_dirs

    # Create dummy PDF file
    dummy_pdf = files_dir / "report.pdf"
    import fitz
    doc = fitz.open()
    for _ in range(3):
        p = doc.new_page()
        p.insert_text((50, 50), "District Health Overview and Statistics")
    doc.save(str(dummy_pdf))
    doc.close()

    from bodhan_mcp.types import OCROutput, TranslateOutput, SpeakOutput

    mock_ocr = OCROutput(text="District Health Overview and Statistics Page 1\nPage 2\nPage 3", page_count=3, tables_detected=1, cost_inr=0.30, cached=False)
    mock_trans = TranslateOutput(translated_text="மாவட்ட சுகாதார கண்ணோட்டம் மற்றும் புள்ளிவிவரங்கள் சுருக்கம்", target_language="ta", estimated_tokens=150, cost_inr=0.01, cached=False)
    mock_speak = SpeakOutput(audio_path=str(out_dir / "artifacts" / "summary_ta.mp3"), duration_seconds=15.0, character_count=65, cost_inr=0.05, cached=False)
    mock_bt = TranscribeOutput(text="மாவட்ட சுகாதார கண்ணோட்டம்", duration_seconds=15.0, cost_inr=0.02, cached=False)

    with patch("bodhan_agent.agent.run_ocr", return_value=mock_ocr), \
         patch("bodhan_agent.agent.translate_text", return_value=mock_trans), \
         patch("bodhan_agent.agent.synthesize_speech", return_value=mock_speak), \
         patch("bodhan_agent.agent.transcribe_audio", return_value=mock_bt):

        # Write dummy audio so file existence check passes
        (out_dir / "artifacts").mkdir(parents=True, exist_ok=True)
        (out_dir / "artifacts" / "summary_ta.mp3").write_bytes(b"\xFF\xFB" + b"\x00" * 1500)

        agent = BodhanTaskAgent(
            task="Process this scanned PDF report, extract text via OCR, summarize in English, translate to Tamil, and synthesize an audio summary",
            files_dir=str(files_dir),
            out_dir=str(out_dir),
            max_cost_inr=5.0,
        )
        success = agent.execute()

    assert success is True
    captured = capsys.readouterr()
    assert "Calling bodhan_ocr" in captured.out
    assert "Calling bodhan_translate" in captured.out
    assert "Calling bodhan_speak" in captured.out
    assert "Calling bodhan_transcribe for back-transcription" in captured.out
    assert "[PASS] document_pages_processed" in captured.out
    assert "[PASS] target_script_match" in captured.out

    with open(out_dir / "report.json", "r", encoding="utf-8") as f:
        rep = json.load(f)
    assert rep["status"] == "completed"
    assert "artifacts/summary_ta.mp3" in rep["artifacts"]
    assert "artifacts/summary_ta.md" in rep["artifacts"]


def test_stage_b_table_translation_mocked(temp_dirs, capsys):
    """Test Stage B Markdown table translation to Marathi with row/col verification (0 paid calls)."""
    files_dir, out_dir = temp_dirs

    dummy_md = files_dir / "table.md"
    dummy_md.write_text(
        "# Report\n\n| Name | Value |\n|---|---|\n| Item 1 | 100 |\n| Item 2 | 200 |\n",
        encoding="utf-8",
    )

    from bodhan_mcp.types import TranslateOutput
    mock_trans = TranslateOutput(
        translated_text="# अहवाल\n\n| नाव | मूल्य |\n|---|---|\n| वस्तू १ | १०० |\n| वस्तू २ | २०० |\n",
        target_language="mr",
        estimated_tokens=50,
        cost_inr=0.005,
        cached=False,
    )

    with patch("bodhan_agent.agent.translate_text", return_value=mock_trans):
        agent = BodhanTaskAgent(
            task="Translate this Markdown document and its statistical table to Marathi while preserving the table structure",
            files_dir=str(files_dir),
            out_dir=str(out_dir),
            max_cost_inr=5.0,
        )
        success = agent.execute()

    assert success is True
    captured = capsys.readouterr()
    assert "Calling bodhan_translate for Markdown table translation" in captured.out
    assert "[PASS] table_count_preservation" in captured.out
    assert "[PASS] row_count_match" in captured.out
    assert "[PASS] column_count_match" in captured.out

    with open(out_dir / "report.json", "r", encoding="utf-8") as f:
        rep = json.load(f)
    assert rep["status"] == "completed"
    assert "artifacts/table_mr.md" in rep["artifacts"]


def test_stage_c_audio_lang_detect_mocked(temp_dirs, capsys):
    """Test Stage C multi-audio language detection with mocks (0 paid calls)."""
    files_dir, out_dir = temp_dirs

    # Create dummy audio files
    (files_dir / "clip1.mp3").write_bytes(b"\xFF\xFB" + b"\x00" * 200)
    (files_dir / "clip2.mp3").write_bytes(b"\xFF\xFB" + b"\x00" * 200)

    from bodhan_mcp.types import TranscribeOutput
    mock_trans_ta = TranscribeOutput(text="வணக்கம் எப்படி இருக்கிறீர்கள்", duration_seconds=3.0, cost_inr=0.005, cached=False)
    mock_trans_te = TranscribeOutput(text="నమస్కారం మీరు ఎలా ఉన్నారు", duration_seconds=3.0, cost_inr=0.005, cached=False)

    with patch("bodhan_agent.agent.transcribe_audio", side_effect=[mock_trans_ta, mock_trans_te]):
        agent = BodhanTaskAgent(
            task="Detect the spoken language for each of these audio clips without language parameters",
            files_dir=str(files_dir),
            out_dir=str(out_dir),
            max_cost_inr=5.0,
        )
        success = agent.execute()

    assert success is True
    captured = capsys.readouterr()
    assert "[PASS] detect_ta" in captured.out
    assert "[PASS] detect_te" in captured.out

    with open(out_dir / "report.json", "r", encoding="utf-8") as f:
        rep = json.load(f)
    assert rep["status"] == "completed"
    assert "artifacts/lang_detect_results.json" in rep["artifacts"]


def test_cli_files_argument_accepts_file_or_directory(tmp_path):
    """Test that find_input_files accepts either a single file or a directory path."""
    input_dir = tmp_path / "clips_folder"
    input_dir.mkdir(parents=True, exist_ok=True)
    file_1 = input_dir / "track1.mp3"
    file_2 = input_dir / "track2.wav"
    file_1.write_bytes(b"\x00" * 50)
    file_2.write_bytes(b"\x00" * 50)

    # 1. Directory test
    agent_dir = BodhanTaskAgent(
        task="Test directory input",
        files_dir=str(input_dir),
        out_dir=str(tmp_path / "out_dir"),
    )
    found_from_dir = agent_dir.find_input_files()
    assert len(found_from_dir) == 2
    assert file_1 in found_from_dir
    assert file_2 in found_from_dir

    # 2. Single file test
    agent_file = BodhanTaskAgent(
        task="Test single file input",
        files_dir=str(file_1),
        out_dir=str(tmp_path / "out_file"),
    )
    found_from_file = agent_file.find_input_files()
    assert len(found_from_file) == 1
    assert found_from_file[0] == file_1


def test_report_json_records_missed_quality_targets(tmp_path):
    """Test that report.json properly records missed quality targets and reasons."""
    from bodhan_agent.tracer import Tracer
    from bodhan_agent.verifier import VerificationResult

    out_dir = tmp_path / "out_tracer_test"
    tracer = Tracer(out_dir=out_dir)

    # Simulate a verification check that resulted in WEAK
    tracer.record_verifications([
        VerificationResult(
            artifact_name="artifacts/summary.mp3",
            check_type="back_transcription_coverage",
            passed=True,
            status="WEAK",
            details="Coverage is 58.2% (<70%), speech may be degraded.",
        )
    ])

    tracer.finalize(
        task="Test quality tracking",
        status="completed",
        total_cost_inr=0.05,
    )

    report_p = out_dir / "report.json"
    assert report_p.exists()
    with open(report_p, "r", encoding="utf-8") as f:
        rep = json.load(f)

    assert "missed_quality_targets" in rep
    assert len(rep["missed_quality_targets"]) >= 1
    miss = rep["missed_quality_targets"][0]
    assert "verification_back_transcription_coverage" in miss["target"]
    assert miss["actual"] == "WEAK"
    assert "58.2%" in miss["reason"]

