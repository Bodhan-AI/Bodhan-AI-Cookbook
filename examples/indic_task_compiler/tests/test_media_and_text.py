"""Unit tests for deterministic media windowing, chunking, LID, CER, repetition, and SRT formatting."""

from __future__ import annotations

from pathlib import Path

from bodhan_mcp.media import compute_audio_windows, detect_silences
from bodhan_mcp.rules import is_language_supported, resolve_language, select_voice
from bodhan_mcp.text_utils import (
    chunk_markdown_for_translation,
    compute_cer,
    contains_indic_script,
    detect_repetition,
    detect_text_language,
    extract_digits,
    extract_markdown_tables,
    format_subtitles,
    split_chunk_in_half,
    split_text_at_sentences,
    validate_subtitles,
)
from tests.fakes import make_segmented_wav


def test_audio_windowing_splits_at_silence_under_28s(tmp_path: Path) -> None:
    # 70 seconds: 20s tone, 2s silence, 22s tone, 2s silence, 24s tone.
    wav = make_segmented_wav(
        tmp_path / "long.wav",
        [("tone", 20.0), ("silence", 2.0), ("tone", 22.0), ("silence", 2.0), ("tone", 24.0)],
    )
    silences = detect_silences(wav, noise_db=-35.0, min_silence_s=0.35)
    assert len(silences) >= 2
    windows = compute_audio_windows(70.0, silences, max_window_s=28.0)
    assert len(windows) >= 3
    for w in windows:
        assert w.duration_s <= 28.0 + 1e-3
    # First split should land inside the first silence interval [20.0, 22.0].
    assert 19.5 <= windows[0].end_s <= 22.5


def test_audio_windowing_hard_split_and_pure_silence_flag() -> None:
    # No silences at all in 65s -> hard splits <= 28s.
    wins = compute_audio_windows(65.0, [], max_window_s=28.0)
    assert len(wins) == 3
    assert all(w.duration_s <= 28.0 for w in wins)
    assert not any(w.is_pure_silence for w in wins)

    # Pure silence file -> flagged pure silence.
    silent_wins = compute_audio_windows(12.0, [(0.0, 12.0)], max_window_s=28.0)
    assert len(silent_wins) == 1
    assert silent_wins[0].is_pure_silence is True


def test_tts_sentence_chunking_respects_indic_punctuation_and_limit() -> None:
    text = (
        "पहला वाक्य हिंदी में है। दूसरा वाक्य भी हिंदी में है। "
        "तीसरा वाक्य बहुत लंबा है " + ("शब्द " * 60) + "समाप्त।"
    )
    chunks = split_text_at_sentences(text, max_chars=250)
    assert len(chunks) >= 2
    assert all(len(c) <= 250 for c in chunks)
    assert "पहला वाक्य" in chunks[0]


def test_translation_chunking_never_splits_markdown_tables_or_code() -> None:
    table = (
        "| Item | Qty | Price |\n"
        "| --- | --- | --- |\n"
        "| Rice | 10 | 500 |\n"
        "| Wheat | 20 | 800 |\n"
        "| Sugar | 5 | 250 |"
    )
    doc = ("Intro paragraph. " * 25) + "\n\n" + table + "\n\n" + ("Outro paragraph. " * 25)
    chunks = chunk_markdown_for_translation(doc, max_chars=260)
    table_chunks = [c for c in chunks if "| Rice |" in c]
    assert len(table_chunks) == 1
    assert "| Item | Qty | Price |" in table_chunks[0]
    assert "| Sugar | 5 | 250 |" in table_chunks[0]
    assert extract_markdown_tables(table_chunks[0]) == [{"rows": 4, "cols": 3}]


def test_split_chunk_in_half_preserves_table_headers() -> None:
    table = (
        "| Name | Score |\n"
        "| --- | --- |\n"
        "| A | 10 |\n"
        "| B | 20 |\n"
        "| C | 30 |\n"
        "| D | 40 |"
    )
    left, right = split_chunk_in_half(table)
    assert extract_markdown_tables(left) == [{"rows": 3, "cols": 2}]
    assert extract_markdown_tables(right) == [{"rows": 3, "cols": 2}]


def test_script_and_stopword_lid_disambiguates_hindi_marathi_bengali_assamese_telugu() -> None:
    hi = detect_text_language("यह विद्यालय कल बंद रहेगा और सभी छात्र घर में रहेंगे।")
    assert hi["language"] == "hi"
    mr = detect_text_language("हे विद्यालय उद्या बंद आहे आणि सर्व विद्यार्थ्यांसाठी सूचना आहे।")
    assert mr["language"] == "mr"
    te = detect_text_language("ఇది తెలుగు భాషలో ఉన్న ఒక ముఖ్యమైన ప్రకటన.")
    assert te["language"] == "te"
    assert te["ambiguous"] is False
    bn = detect_text_language("এই বিদ্যালয়টি আগামীকাল বন্ধ থাকবে এবং ছাত্রদের জন্য ছুটি আছে।")
    assert bn["language"] == "bn"
    as_det = detect_text_language("এইটো অসমীয়া ভাষা আৰু সকলোৰে বাবে পৰা কৰা হৈছে।")
    assert as_det["language"] == "as"


def test_language_resolution_and_voice_selection_tables() -> None:
    assert resolve_language("Tamil") == ("ta", True)
    assert resolve_language("Bangla") == ("bn", True)
    assert resolve_language("French") == ("fr", False)
    assert is_language_supported("indic-translate", "ta") is True
    assert is_language_supported("indic-translate", "fr") is False
    assert is_language_supported("indic-translate", "bho") is False  # bho only in transcribe
    assert is_language_supported("indic-transcribe", "bho") is True

    voice, err = select_voice("bn", gender="female")
    assert err is None and voice == "Ishita"
    bad_voice, bad_err = select_voice("bn", voice="NonExistentVoice")
    assert bad_voice is None and bad_err is not None


def test_cer_repetition_digits_and_indic_detection() -> None:
    assert compute_cer("வணக்கம் நண்பா", "வணக்கம் நண்பா") == 0.0
    assert compute_cer("வணக்கம் நண்பா", "வணக்கம்") > 0.3
    rep, phrase = detect_repetition("hello world test hello world test hello world test")
    assert rep is True and phrase is not None
    no_rep, _ = detect_repetition("The rain in Spain stays mainly in the plain.")
    assert no_rep is False
    assert extract_digits("वर्ष २०२६ में 45 छात्र") == ["2026", "45"]
    assert contains_indic_script("Hello नमस्ते") is True
    assert contains_indic_script("Hello world 123!") is False


def test_subtitle_formatting_and_validation() -> None:
    segments = [
        {"start_s": 0.0, "end_s": 10.0, "text": "First sentence here. " * 8},
        {"start_s": 10.0, "end_s": 18.5, "text": "Second short segment."},
    ]
    srt, count = format_subtitles(segments, fmt="srt", max_cue_chars=84)
    assert count >= 3
    val = validate_subtitles(srt)
    assert val["valid"] is True
    assert val["monotonic"] is True
    assert val["cue_count"] == count
