"""End-to-end agent tests against the mock Bodhan client + mock LLM.

Run:  pytest -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bodhan_agent.agent import Agent, AgentConfig

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "sample_data"


def run_agent(task: str, files: str, tmp_path, **overrides) -> tuple[dict, Path]:
    out = tmp_path / "out"
    config = AgentConfig(out_dir=str(out), mock=True, **overrides)
    report = Agent(config).run(task, files)
    return report, out


def test_voice_note_transcribe_and_translate(tmp_path):
    report, out = run_agent(
        "Transcribe the Hindi voice note and translate it into English.",
        str(SAMPLE / "voice_note_hi.wav"), tmp_path,
    )
    assert report["status"] == "completed"
    assert report["total_cost_inr"] < 1.0
    transcript = (out / "artifacts" / "transcript.txt").read_text(encoding="utf-8")
    assert "सुजल" in transcript
    translation = (out / "artifacts" / "translation.en.md").read_text(encoding="utf-8")
    assert "Sujal" in translation
    assert (out / "result.md").exists()
    assert (out / "trace.jsonl").exists()
    assert (out / "report.json").exists()
    checks = {c["check"]: c["status"] for c in report["verification"]}
    assert checks.get("translation_coverage") == "passed"


def test_chapter_ocr_summarize_translate_speak(tmp_path):
    report, out = run_agent(
        "Read the chapter PDF, summarize it, translate the summary into "
        "Tamil and read it aloud with a female voice.",
        str(SAMPLE / "chapter.pdf"), tmp_path,
    )
    assert report["status"] == "completed"
    assert (out / "artifacts" / "source.md").exists()
    assert "Lines and Angles" in (out / "artifacts" / "source.md").read_text()
    translation = (out / "artifacts" / "translation.ta.md").read_text(encoding="utf-8")
    assert "கோடுகள்" in translation
    audio = out / "artifacts" / "speech.ta.wav"
    assert audio.exists() and audio.stat().st_size > 1000
    checks = {c["check"]: c["status"] for c in report["verification"]}
    assert checks.get("audio_fidelity") in ("passed", "passed_after_retry")
    assert checks.get("translation_coverage") == "passed"


def test_paragraph_translate_and_speak_hindi(tmp_path):
    report, out = run_agent(
        "Translate the paragraph into Hindi and read it aloud.",
        str(SAMPLE / "paragraph.txt"), tmp_path,
    )
    assert report["status"] == "completed"
    translation = (out / "artifacts" / "translation.hi.md").read_text(encoding="utf-8")
    assert "नमस्ते" in translation
    assert (out / "artifacts" / "speech.hi.wav").exists()


def test_identify_telugu_clips_and_translate(tmp_path):
    report, out = run_agent(
        "Listen to the clips and tell me which ones are Telugu; "
        "translate those into English.",
        str(SAMPLE / "clips"), tmp_path,
    )
    assert report["status"] == "completed"
    translation = (out / "artifacts" / "translation.en.md").read_text(encoding="utf-8")
    # Both Telugu clips were translated (appended, not overwritten).
    assert "Hyderabad" in translation
    assert "mother tongue" in translation
    # The Hindi and English clips must NOT have been translated.
    assert "Delhi" not in translation


def test_subtitle_task_produces_srt(tmp_path):
    report, out = run_agent(
        "Transcribe the Hindi voice note and translate it into English "
        "with subtitles.",
        str(SAMPLE / "voice_note_hi.wav"), tmp_path,
    )
    assert report["status"] == "completed"
    srt = out / "artifacts" / "subtitles.en.srt"
    assert srt.exists()
    text = srt.read_text(encoding="utf-8")
    assert "00:00:00,000 -->" in text
    assert "Sujal" in text


def test_french_translation_refused_honestly(tmp_path):
    report, out = run_agent(
        "Translate the video into French.",
        str(SAMPLE / "sample_video.mp4"), tmp_path,
    )
    assert report["status"] == "failed"
    assert report["error"]["code"] == "unsupported_language"
    assert "French" in report["error"]["message"]
    assert "fabricat" in report["error"]["message"]
    # No translation artifact was produced.
    assert not (out / "artifacts" / "translation.fr.md").exists()
    result_md = (out / "result.md").read_text(encoding="utf-8")
    assert "French" in result_md


def test_missing_input_file_fails_cleanly(tmp_path):
    report, out = run_agent(
        "Transcribe the audio.", str(SAMPLE / "does_not_exist.wav"), tmp_path
    )
    assert report["status"] == "failed"
    assert report["error"]["code"] == "file_not_found"


def test_tiny_budget_stops_before_spending(tmp_path):
    report, out = run_agent(
        "Transcribe the Hindi voice note and translate it into English.",
        str(SAMPLE / "voice_note_hi.wav"), tmp_path,
        max_cost_inr=0.001,
    )
    assert report["status"] == "failed"
    assert report["error"]["code"] == "budget_exceeded"
    assert report["total_cost_inr"] == pytest.approx(0.0)


def test_report_json_is_well_formed(tmp_path):
    report, out = run_agent(
        "Transcribe the Hindi voice note.",
        str(SAMPLE / "voice_note_hi.wav"), tmp_path,
    )
    on_disk = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert on_disk["status"] == report["status"]
    assert on_disk["total_cost_inr"] == report["total_cost_inr"]
    trace = (out / "trace.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(trace) == report["steps"]
    for line in trace:
        json.loads(line)
