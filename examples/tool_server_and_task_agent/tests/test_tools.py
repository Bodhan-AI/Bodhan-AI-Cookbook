"""Unit tests for the Bodhan tools (mock client only — no network, no cost).

Run:  pytest -q
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from bodhan_mcp import languages, media, tools
from bodhan_mcp.client import get_client
from bodhan_mcp.errors import ToolError

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "sample_data"


@pytest.fixture()
def client():
    return get_client(mock=True)


def _write_tone(path: Path, seconds: float, freq: float = 440.0) -> Path:
    return media.synth_tone_wav(path, seconds, freq=freq)


# --- media / chunking -------------------------------------------------------
def test_split_wav_short_file_returns_original(tmp_path):
    wav = _write_tone(tmp_path / "short.wav", 5.0)
    assert media.split_wav(wav, 30.0, tmp_path) == [wav]


def test_split_wav_long_file_chunks_at_30s(tmp_path):
    wav = _write_tone(tmp_path / "long.wav", 65.0)
    chunks = media.split_wav(wav, 30.0, tmp_path)
    assert len(chunks) == 3
    durations = [media.wav_duration(c) for c in chunks]
    assert durations[0] == pytest.approx(30.0)
    assert durations[1] == pytest.approx(30.0)
    assert durations[2] == pytest.approx(5.0)


def test_inspect_file_kinds():
    assert media.inspect_file(SAMPLE / "voice_note_hi.wav")["kind"] == "audio"
    assert media.inspect_file(SAMPLE / "chapter.pdf")["kind"] == "document"
    assert media.inspect_file(SAMPLE / "chapter.pdf")["pages"] == 2
    assert media.inspect_file(SAMPLE / "worksheet.png")["kind"] == "image"
    assert media.inspect_file(SAMPLE / "paragraph.txt")["kind"] == "text"
    assert media.inspect_file(SAMPLE / "sample_video.mp4")["kind"] == "video"


def test_require_file_missing_raises():
    with pytest.raises(ToolError) as excinfo:
        media.require_file("/nonexistent/nope.wav")
    assert excinfo.value.code == "file_not_found"


# --- transcribe ---------------------------------------------------------------
def test_transcribe_hindi_voice_note(client):
    result = tools.transcribe_audio(client, str(SAMPLE / "voice_note_hi.wav"))
    assert result["language"] == "hi"
    assert "सुजल" in result["text"]
    assert result["chunks"] == 1
    assert len(result["segments"]) == 1
    assert result["segments"][0]["start"] == 0.0


def test_transcribe_long_audio_joins_chunks_and_keeps_timestamps(tmp_path, client):
    wav = _write_tone(tmp_path / "clip_01.wav", 65.0)
    result = tools.transcribe_audio(client, str(wav))
    assert result["chunks"] == 3
    assert len(result["segments"]) == 3
    assert result["segments"][1]["start"] == pytest.approx(30.0)
    # The mock keys the transcript on the file stem, so chunked files keep
    # the right transcript instead of a "[mock ...]" placeholder.
    assert "హైదరాబాద్" in result["text"]


def test_transcribe_rejects_non_audio(client):
    with pytest.raises(ToolError) as excinfo:
        tools.transcribe_audio(client, str(SAMPLE / "chapter.pdf"))
    assert excinfo.value.code == "unsupported_media"


def test_transcribe_rejects_unknown_language_code(client):
    with pytest.raises(ToolError) as excinfo:
        tools.transcribe_audio(client, str(SAMPLE / "voice_note_hi.wav"), language="xx")
    assert excinfo.value.code == "unsupported_language"


# --- translate ---------------------------------------------------------------
def test_translate_short_text_uses_canned_mock(client):
    result = tools.translate_text(client, "Hello, my name is Sujal.", "hi")
    assert result["translation"] == "नमस्ते, मेरा नाम सुजल है।"
    assert result["truncated"] is False


def test_translate_empty_text_rejected(client):
    with pytest.raises(ToolError) as excinfo:
        tools.translate_text(client, "   ", "hi")
    assert excinfo.value.code == "invalid_input"


def test_translate_french_refused(client):
    with pytest.raises(ToolError) as excinfo:
        tools.translate_text(client, "Hello.", "fr")
    assert excinfo.value.code == "unsupported_language"


def test_translate_bhojpuri_refused(client):
    with pytest.raises(ToolError) as excinfo:
        tools.translate_text(client, "Hello.", "bho")
    assert excinfo.value.code == "unsupported_language"


def test_translate_roman_script_rejected_for_english(client):
    with pytest.raises(ToolError) as excinfo:
        tools.translate_text(client, "Hello.", "en", script="roman")
    assert excinfo.value.code == "invalid_input"


def test_translate_long_text_splits_at_sentence_boundaries(client):
    text = " ".join(["This is a sentence about lines and angles."] * 40)
    result = tools.translate_text(client, text, "ta")
    assert result["translation"]
    assert result["truncated"] is False
    assert result["output_tokens"] > 0


# --- synthesize ---------------------------------------------------------------
def test_synthesize_writes_wav_and_back_transcribes(client, tmp_path):
    out = tmp_path / "speech.wav"
    result = tools.synthesize_speech(
        client, "Hello, my name is Sujal.", "en", out_path=str(out)
    )
    assert Path(result["audio_path"]).exists()
    assert result["voice"] == "Kavya"  # default female voice for English
    assert result["language"] == "en"
    with wave.open(str(out), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 24000
    back = tools.transcribe_audio(client, str(out))
    assert "Sujal" in back["text"]


def test_synthesize_long_text_joins_chunks(client, tmp_path):
    text = " ".join(["This is a longer sentence for speech synthesis."] * 12)
    result = tools.synthesize_speech(
        client, text, "hi", out_path=str(tmp_path / "long.wav")
    )
    assert result["chunks"] > 1
    assert Path(result["audio_path"]).exists()


def test_synthesize_rejects_unknown_voice(client):
    with pytest.raises(ToolError) as excinfo:
        tools.synthesize_speech(client, "Hello.", "en", voice="Nobody")
    assert excinfo.value.code == "invalid_input"


def test_synthesize_rejects_unsupported_language(client):
    with pytest.raises(ToolError) as excinfo:
        tools.synthesize_speech(client, "Bonjour.", "fr")
    assert excinfo.value.code == "unsupported_language"


# --- OCR ----------------------------------------------------------------------
def test_ocr_worksheet_table(client):
    result = tools.ocr_document(client, str(SAMPLE / "worksheet.png"))
    assert "Asha" in result["markdown"]
    assert "85" in result["markdown"]
    assert result["page_count"] == 1
    assert result["blocks"] >= 1


def test_ocr_pdf_renders_two_pages(client):
    result = tools.ocr_document(client, str(SAMPLE / "chapter.pdf"))
    assert result["page_count"] == 2
    assert "Lines and Angles" in result["markdown"]
    assert "Parallel lines" in result["markdown"]


def test_ocr_rejects_audio(client):
    with pytest.raises(ToolError) as excinfo:
        tools.ocr_document(client, str(SAMPLE / "voice_note_hi.wav"))
    assert excinfo.value.code == "unsupported_media"


# --- identify_language ----------------------------------------------------------
def test_identify_tamil_text_pins_language(client):
    result = tools.identify_language(client, text="வணக்கம், நான் சென்னையில் இருக்கிறேன்.")
    assert result["language"] == "ta"
    assert result["script"] == "Tamil"


def test_identify_devanagari_text_is_ambiguous(client):
    result = tools.identify_language(client, text="आज मौसम बहुत अच्छा है।")
    assert result["language"] is None  # Devanagari is shared by several languages
    assert "hi" in result["candidates"]


def test_identify_audio_clip(client):
    result = tools.identify_language(
        client, audio_file=str(SAMPLE / "clips" / "clip_01.wav")
    )
    assert result["language"] == "te"
    assert "హైదరాబాద్" in result["transcript"]


# --- languages ------------------------------------------------------------------
def test_default_voice_known():
    assert languages.default_voice("ta", "female") == "Anitha"
    assert languages.default_voice("hi", "male") == "Amit"


def test_script_detection():
    assert languages.detect_script("வணக்கம்")["script"] == "Tamil"
    assert languages.detect_script("hello")["script"] == "Latin"


# --- cost accounting --------------------------------------------------------------
def test_mock_costs_are_positive_and_small(client):
    stt = tools.transcribe_audio(client, str(SAMPLE / "voice_note_hi.wav"))
    assert 0 < stt["cost_inr"] < 0.05
    tr = tools.translate_text(client, "Hello, my name is Sujal.", "hi")
    assert 0 < tr["cost_inr"] < 0.05
    tts = tools.synthesize_speech(client, "Hello.", "en")
    assert 0 < tts["cost_inr"] < 0.1
    ocr = tools.ocr_document(client, str(SAMPLE / "worksheet.png"))
    assert ocr["cost_inr"] == pytest.approx(0.20)


def test_repeated_calls_hit_cache_and_cost_zero(client):
    first = tools.transcribe_audio(client, str(SAMPLE / "voice_note_hi.wav"))
    assert first["cached"] is False
    assert first["cost_inr"] > 0
    second = tools.transcribe_audio(client, str(SAMPLE / "voice_note_hi.wav"))
    assert second["cached"] is True
    assert second["cost_inr"] == 0.0
    t1 = tools.translate_text(client, "Hello, my name is Sujal.", "hi")
    assert t1["cached"] is False
    t2 = tools.translate_text(client, "Hello, my name is Sujal.", "hi")
    assert t2["cached"] is True
    assert t2["cost_inr"] == 0.0
    tools.ocr_document(client, str(SAMPLE / "worksheet.png"))
    o2 = tools.ocr_document(client, str(SAMPLE / "worksheet.png"))
    assert o2["cached"] is True
    assert o2["cost_inr"] == 0.0
    assert client.cache_hits >= 3


def test_cost_tracker_counts_cache_hits():
    from bodhan_agent.agent import CostTracker

    tracker = CostTracker(limit_inr=20.0)
    tracker.add(0.05)
    tracker.add(0.0, cached=True)
    assert tracker.spent_inr == pytest.approx(0.05)
    assert tracker.cache_hits == 1
