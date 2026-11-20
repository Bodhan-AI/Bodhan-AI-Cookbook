"""Tests for language tables, text splitting and audio helpers. No network, no cost."""

import io
import shutil
import subprocess
import wave

import pytest

from bodhan_mcp.errors import BodhanError
from bodhan_mcp.languages import (
    detect_language, normalize_language, pick_voice, support_report, supports,
)
from bodhan_mcp.media import inspect_media, join_wavs, plan_chunks, prepare_for_transcribe
from bodhan_mcp.textops import clean_for_speech, split_text

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


# ---- languages ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,code", [
    ("Hindi", "hi"), ("hindi", "hi"), ("HI", "hi"), ("hi_IN", "hi"), ("Tamil", "ta"),
    ("ks-deva", "ks-Deva"), ("Bangla", "bn"), ("Oriya", "or"), ("Kashmiri", "ks"),
])
def test_normalize_language(raw, code):
    assert normalize_language(raw) == code


@pytest.mark.parametrize("raw", ["French", "fr", "", None, "klingon"])
def test_unknown_languages_are_none(raw):
    assert normalize_language(raw) is None


def test_french_is_unsupported_everywhere():
    r = support_report("French")
    assert r["recognized"] is False


def test_support_differs_per_model():
    assert supports("transcribe", "bho") and not supports("translate", "bho")
    assert supports("translate", "ks-Deva") and not supports("speak", "ks-Deva")
    r = support_report("Bhojpuri")
    assert (r["transcribe"], r["translate"], r["speak"]) == (True, False, False)


def test_pick_voice():
    assert pick_voice("hi") == "Kavya"  # default: first female
    assert pick_voice("hi", gender="male") == "Amit"
    assert pick_voice("bn", voice="sourav") == "Sourav"  # case-insensitive
    with pytest.raises(ValueError, match="Choose from"):
        pick_voice("hi", voice="Nobody")
    with pytest.raises(ValueError):
        pick_voice("bho")  # no voices


@pytest.mark.parametrize("text,script,lang", [
    ("बैठक कल तक स्थगित", "Devanagari", "hi"),
    ("நாளை கூட்டம்", "Tamil", "ta"),
    ("సమావేశం రేపు", "Telugu", "te"),
    ("The meeting is tomorrow", "Latin", "en"),
    ("ਮੀਟਿੰਗ ਕੱਲ੍ਹ ਹੈ", "Gurmukhi", "pa"),
])
def test_detect_language_by_script(text, script, lang):
    d = detect_language(text)
    assert d["script"] == script and d["language"] == lang


def test_devanagari_is_flagged_ambiguous_and_marathi_letter_helps():
    assert detect_language("बैठक कल")["ambiguous"] is True
    assert detect_language("आळशी माणूस")["language"] == "mr"  # ळ


def test_assamese_letters_separate_from_bengali():
    assert detect_language("ৰ ৱ আমি")["language"] == "as"
    assert detect_language("আমি ভাত খাই")["language"] == "bn"


def test_detect_on_empty_or_numbers():
    assert detect_language("123 !!!")["script"] is None


# ---- text splitting ----------------------------------------------------------------------
def test_split_respects_limit_and_keeps_all_words():
    text = "यह पहला वाक्य है। यह दूसरा वाक्य है। " * 20
    pieces = split_text(text, 80)
    assert all(len(p) <= 80 for p in pieces) and len(pieces) > 1
    assert " ".join(pieces).split() == text.split()


def test_split_prefers_sentence_ends():
    pieces = split_text("One. Two. Three.", 7)
    assert pieces == ["One.", "Two.", "Three."]


def test_split_handles_long_sentence_and_giant_word():
    assert all(len(p) <= 20 for p in split_text("word " * 50, 20))
    giant = split_text("x" * 95, 30)
    assert [len(g) for g in giant] == [30, 30, 30, 5]


def test_split_empty():
    assert split_text("   ", 100) == []


def test_clean_for_speech_removes_markup():
    raw = "## Title\n**bold** and <b>tag</b> [link](http://x.y) | cell |"
    out = clean_for_speech(raw)
    assert "#" not in out and "*" not in out and "<" not in out and "http" not in out
    assert "Title" in out and "bold" in out and "link" in out


# ---- chunk planning (pure) ---------------------------------------------------------------
def test_short_audio_is_one_chunk():
    assert plan_chunks(20.0, []) == [(0.0, 20.0)]


def test_cuts_in_silences_and_respects_max():
    silences = [(12.0, 12.6), (25.0, 25.4), (40.0, 40.5), (55.0, 55.6)]
    chunks = plan_chunks(70.0, silences)
    assert chunks[0][0] == 0.0 and chunks[-1][1] == 70.0
    assert all(b - a <= 28.0 + 1e-6 for a, b in chunks)
    assert all(chunks[i][1] == chunks[i + 1][0] for i in range(len(chunks) - 1))  # no gaps
    assert chunks[0][1] == pytest.approx(25.2)  # latest silence inside the window


def test_hard_cut_when_no_silence():
    chunks = plan_chunks(60.0, [])
    assert chunks == [(0.0, 28.0), (28.0, 56.0), (56.0, 60.0)]


# ---- real ffmpeg -------------------------------------------------------------------------
def _make_speechlike(path, seconds=70):
    """Tone bursts separated by 0.8 s silences, so there are real silences to cut in."""
    expr = "if(lt(mod(t\\,10)\\,9.2)\\,sin(2*PI*440*t)*0.5\\,0)"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"aevalsrc={expr}:s=44100:d={seconds}",
         "-c:a", "libmp3lame", str(path)], check=True, capture_output=True)


@needs_ffmpeg
def test_inspect_media_on_audio(tmp_path):
    f = tmp_path / "t.mp3"
    _make_speechlike(f, 12)
    info = inspect_media(f)
    assert info["kind"] == "audio" and 11 < info["duration_s"] < 13


@needs_ffmpeg
def test_prepare_for_transcribe_cuts_a_long_file_into_valid_chunks(tmp_path):
    f = tmp_path / "long.mp3"
    _make_speechlike(f, 70)
    chunks = prepare_for_transcribe(f, tmp_path / "work")
    assert len(chunks) >= 3
    total = 0.0
    for c in chunks:
        with wave.open(str(c["path"])) as w:
            dur = w.getnframes() / w.getframerate()
            assert w.getframerate() == 16000 and w.getnchannels() == 1
        assert dur <= 28.5
        total += dur
    assert total == pytest.approx(70.0, abs=1.0)  # nothing lost


def test_missing_file_gives_structured_error(tmp_path):
    with pytest.raises(BodhanError) as e:
        inspect_media(tmp_path / "nope.wav")
    assert e.value.code == "file_not_found"


def _wav(seconds, rate=24000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x00" * int(rate * seconds))
    return buf.getvalue()


def test_join_wavs_adds_gap(tmp_path):
    out = join_wavs([_wav(1.0), _wav(2.0)], tmp_path / "j.wav", gap_ms=500)
    with wave.open(str(out)) as w:
        assert w.getnframes() / w.getframerate() == pytest.approx(3.5)


def test_join_wavs_rejects_mixed_formats(tmp_path):
    with pytest.raises(BodhanError) as e:
        join_wavs([_wav(1.0, 24000), _wav(1.0, 16000)], tmp_path / "j.wav")
    assert e.value.code == "audio_format_mismatch"