"""Deterministic chunking, limits and language helpers."""

import numpy as np
import pytest

from bodhan_mcp import config, lang, media
from bodhan_mcp.errors import ToolError
from conftest import make_tone_wav


def test_audio_windows_respect_limit_and_cut_at_silence(tmp_path):
    p = make_tone_wav(tmp_path / "a.wav", [(9, 6000), (0.6, 0)] * 7)
    windows = media.split_audio(media.decode_to_pcm(p))
    assert len(windows) >= 3
    assert all(w.duration <= config.ASR_WINDOW_SEC + 0.05 for w in windows)
    # every internal cut falls inside a pause (pauses start at multiples of 9.6 s, + 9 s)
    for w in windows[:-1]:
        offset = w.end % 9.6
        assert 8.9 <= offset <= 9.65, w.end
    assert windows[-1].end == pytest.approx(9.6 * 7, abs=0.06)


def test_silence_is_never_sent(tmp_path):
    p = make_tone_wav(tmp_path / "s.wav", [(40, 0)])
    assert media.split_audio(media.decode_to_pcm(p)) == []


def test_long_unbroken_audio_is_still_split():
    pcm = (np.sin(np.arange(16000 * 65) / 3) * 5000).astype(np.int16)
    ws = media.split_audio(pcm)
    assert len(ws) >= 3 and all(w.duration <= config.ASR_WINDOW_SEC + 0.05 for w in ws)
    assert ws[0].start == 0 and ws[-1].end == 65 and all(a.end == b.start for a, b in zip(ws, ws[1:], strict=False))


def test_tts_sentences_are_short():
    text = "पहला वाक्य। " + ("यह एक बहुत लंबा वाक्य है जिसमें कई शब्द हैं, " * 20) + "अंत।"
    pieces = media.split_sentences(text)
    assert all(len(p) <= config.TTS_MAX_CHARS for p in pieces)
    assert pieces[0] == "पहला वाक्य।" and pieces[-1].endswith("अंत।")
    assert "".join(pieces).replace(" ", "") == text.replace(" ", "")


def test_translation_chunks_keep_tables_whole():
    table = "| a | b |\n|---|---|\n" + "\n".join(f"| {i} | {i * 2} |" for i in range(50))
    text = ("Intro paragraph. " * 200) + "\n\n" + table + "\n\nOutro."
    chunks = media.split_blocks(text, max_chars=1500)
    assert len(chunks) > 2
    assert sum(table in c for c in chunks) == 1
    assert all(len(c) <= 1500 or c.startswith("|") for c in chunks)


def test_pdf_pages_render(media_dir):
    assert len(media.pdf_pages(media_dir / "chapter.pdf")) == 3


@pytest.mark.parametrize("text,expected", [
    ("यह हिंदी है", "hi"), ("இது தமிழ்", "ta"), ("এটা বাংলা", "bn"), ("ఇది తెలుగు", "te"),
    ("This is English", "en"), ("मी शाळेत जातो आहे", "mr"),
])
def test_script_language(text, expected):
    assert lang.detect_script_language(text)["language"] == expected


def test_language_names_and_unsupported():
    assert lang.normalize_code("Hindi", "indic-translate") == "hi"
    assert lang.normalize_code("ta", "indic-speak") == "ta"
    with pytest.raises(ToolError) as e:
        lang.normalize_code("French", "indic-translate")
    assert e.value.code == "unsupported_language"
    assert lang.normalize_code(None, "indic-transcribe") is None


def test_cer_and_numbers():
    assert lang.cer("नमस्ते दुनिया", "नमस्ते दुनिया") == 0
    assert lang.cer("abc", "abcabcabc") == 2.0
    assert lang.numbers_in("कीमत ₹१,२०० और 25 kg") == ["1200", "25"]
