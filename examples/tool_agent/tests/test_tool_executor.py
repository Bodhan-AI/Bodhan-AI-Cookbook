from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import bodhan_agent.tools as tools
from bodhan_agent.errors import ToolError
from bodhan_agent.settings import Settings
from bodhan_agent.tools import ToolExecutor


@pytest.fixture
def executor(tmp_path):
    settings = Settings("transcribe-key", "translate-key", "speak-key", "ocr-key", "", "", "")
    instance = ToolExecutor(settings, tmp_path / "artifacts")
    instance.cache_dir = tmp_path / "cache"
    instance.cache_dir.mkdir()
    instance.cache_db = instance.cache_dir / "tool_cache.sqlite3"
    with sqlite3.connect(instance.cache_db) as db:
        db.execute("CREATE TABLE results (cache_key TEXT PRIMARY KEY, result_json TEXT NOT NULL)")
    return instance


def test_translation_segments_deduplicate_and_preserve_timing(monkeypatch, executor):
    calls = []

    def translate(text, target_language, settings, *, script):
        calls.append((text, target_language, script))
        return {"translation": f"out:{text}", "cost_inr": 0.01, "verification": {"passed": True}}

    monkeypatch.setattr(tools, "translate_text", translate)
    monkeypatch.setattr(executor, "_cached", lambda key: None)
    monkeypatch.setattr(executor, "_store", lambda *args: None)
    segments = [
        {"start": 0.0, "end": 1.5, "text": "same", "timing_estimated": True},
        {"start": 1.5, "end": 3.0, "text": "same", "timing_estimated": True},
        {"start": 3.0, "end": 4.0, "text": "different", "timing_estimated": False},
    ]

    result = executor.call(
        "translate_segments",
        {"segments": segments, "target_language": "hi", "script": "native"},
        max_cost_inr=1.0,
        spent_inr=0.0,
    )

    assert calls == [("same", "hi", "native"), ("different", "hi", "native")]
    assert [(item["start"], item["end"]) for item in result["segments"]] == [(0.0, 1.5), (1.5, 3.0), (3.0, 4.0)]
    assert [item["text"] for item in result["segments"]] == ["out:same", "out:same", "out:different"]
    assert result["segments"][0]["timing_estimated"] is True
    assert result["segments"][2]["timing_estimated"] is False
    assert result["cost_inr"] == pytest.approx(0.02)


def test_budget_is_checked_before_tool_execution(monkeypatch, executor):
    monkeypatch.setattr(executor, "_estimate_cost", lambda name, arguments: 1.25)
    monkeypatch.setattr(tools, "translate_text", lambda *args, **kwargs: pytest.fail("API tool must not run over budget"))

    with pytest.raises(ToolError, match="would exceed remaining budget") as error:
        executor.call(
            "translate_text",
            {"text": "Hello", "target_language": "hi"},
            max_cost_inr=1.0,
            spent_inr=0.0,
        )
    assert error.value.code == "budget_exceeded"


def test_cached_tool_result_has_zero_additional_cost(monkeypatch, executor):
    cached = {"translation": "cached result", "cost_inr": 0.5, "verification": {"passed": True}}
    monkeypatch.setattr(executor, "_cached", lambda key: dict(cached))
    monkeypatch.setattr(tools, "translate_text", lambda *args, **kwargs: pytest.fail("cache hit should skip API"))
    result = executor.call(
        "translate_text",
        {"text": "Hello", "target_language": "hi"},
        max_cost_inr=1.0,
        spent_inr=0.0,
    )

    assert result["translation"] == "cached result"
    assert result["cached"] is True
    assert result["cost_inr"] == 0.0


def test_file_cache_key_changes_when_input_changes(executor, tmp_path):
    path = tmp_path / "scan.png"
    path.write_bytes(b"first image")
    first_key = executor._cache_key("ocr_document", {"file_ref": str(path)})
    path.write_bytes(b"updated image")
    second_key = executor._cache_key("ocr_document", {"file_ref": str(path)})
    assert first_key != second_key


def test_cost_estimates_cover_all_billed_model_tools(monkeypatch, executor, tmp_path):
    translate_cost = executor._estimate_cost("translate_text", {"text": "x" * 50})
    segment_cost = executor._estimate_cost(
        "translate_segments",
        {"segments": [{"text": "x" * 10}, {"text": "y" * 5}]},
    )
    speech_cost = executor._estimate_cost("synthesize_speech", {"text": "x" * 60})
    assert translate_cost == pytest.approx(50 * 0.20 / 10000)
    assert segment_cost == pytest.approx(15 * 0.20 / 10000)
    assert speech_cost == pytest.approx(60 * 6 / 10000 + (60 / 10 / 60) * 0.10)

    pages = tmp_path / "ocr-pages"
    pages.mkdir()
    for name in ("one.png", "two.jpg", "three.webp", "notes.txt"):
        (pages / name).write_bytes(b"x")
    ocr_cost = executor._estimate_cost("ocr_document", {"file_ref": str(pages)})
    assert ocr_cost == pytest.approx(2 * 0.20)

    audio_file = tmp_path / "minute.wav"
    import wave
    with wave.open(str(audio_file), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\0" * 24000 * 60)
    transcribe_cost = executor._estimate_cost("transcribe_audio", {"file_ref": str(audio_file)})
    assert transcribe_cost == pytest.approx(0.10)


def test_pdf_cost_estimate_uses_document_page_count(monkeypatch, executor, tmp_path):
    pdf = tmp_path / "scanned.pdf"
    pdf.write_bytes(b"pdf")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout="Pages: 4\n"))
    assert executor._estimate_cost("ocr_document", {"file_ref": str(pdf)}) == pytest.approx(0.8)


def test_write_srt_creates_artifact_and_blocks_outside_path(executor, tmp_path):
    arguments = {
        "segments": [{"start": 0.0, "end": 1.2, "text": "Hello."}],
        "output_path": str(executor.artifacts_dir / "captions.srt"),
    }
    result = executor.call("write_srt", arguments, max_cost_inr=1.0, spent_inr=0.0)
    assert Path(result["artifact_ref"]).read_text(encoding="utf-8").startswith("1\n00:00:00,000 --> 00:00:01,200")

    arguments["output_path"] = str(tmp_path / "outside.srt")
    with pytest.raises(ToolError, match="inside this run's artifacts"):
        executor.call("write_srt", arguments, max_cost_inr=1.0, spent_inr=0.0)


def test_script_identifier_distinguishes_hindi_and_kannada(executor):
    hindi = executor.call(
        "identify_language_from_script",
        {"text": "कल मैं बस से पास के शहर जाऊँगा", "candidate_languages": ["hi", "kn"]},
        max_cost_inr=0.0,
        spent_inr=0.0,
    )
    kannada = executor.call(
        "identify_language_from_script",
        {"text": "ಇಂದು ಬೆಳಗ್ಗೆ ನಮ್ಮ ಊರಿನ ಮಾರುಕಟ್ಟೆಗೆ ಹೋಗುತ್ತಿದ್ದೇನೆ", "candidate_languages": ["hi", "kn"]},
        max_cost_inr=0.0,
        spent_inr=0.0,
    )

    assert hindi["language_code"] == "hi"
    assert hindi["script"] == "Devanagari"
    assert kannada["language_code"] == "kn"
    assert kannada["script"] == "Kannada"


def test_script_identifier_does_not_guess_between_shared_script_languages(executor):
    result = executor.call(
        "identify_language_from_script",
        {"text": "कल मैं पुस्तक पढ़ रहा हूँ", "candidate_languages": ["hi", "mr"]},
        max_cost_inr=0.0,
        spent_inr=0.0,
    )

    assert result["status"] == "ambiguous"
    assert result["language_code"] is None
    assert result["candidates"] == ["hi", "mr"]


def test_unknown_tool_returns_structured_tool_error(executor):
    with pytest.raises(ToolError, match="Unknown tool") as error:
        executor.call("not_a_tool", {}, max_cost_inr=1.0, spent_inr=0.0)
    assert error.value.code == "unknown_tool"
