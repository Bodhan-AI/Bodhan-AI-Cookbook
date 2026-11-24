"""langid.py — script identification, including the honest non-answers."""
from __future__ import annotations

import pytest

from bodhan_mcp.core import langid
from bodhan_mcp.core.errors import BodhanError

TAMIL = "தமிழ் மொழி மிகவும் அழகான மொழி. இது தமிழ்நாட்டின் அதிகாரப்பூர்வ மொழி ஆகும்."
DEVANAGARI = "यह एक हिंदी वाक्य है। मराठी और नेपाली भी देवनागरी में लिखी जाती हैं।"
BENGALI = "এটি একটি বাংলা বাক্য। অসমীয়াও এই লিপি ব্যবহার করে।"
ENGLISH = "This is an English sentence written with plain ASCII letters."
MIXED = "தமிழ் மொழி அழகானது. This sentence is half English."


def test_tamil_is_decisive():
    guess = langid.identify(TAMIL)
    assert guess["language"] == "ta"
    assert guess["script"] == "Tamil"
    assert guess["confidence"] > 0.8
    assert guess["candidates"] == ["ta"]
    assert guess["chars_sampled"] == len(TAMIL)


def test_devanagari_returns_candidates_not_a_guess():
    guess = langid.identify(DEVANAGARI)
    assert guess["language"] is None
    assert guess["script"] == "Devanagari"
    assert set(guess["candidates"]) == {"hi", "mr", "ne", "sa"}
    assert guess["confidence"] < 0.5


def test_bengali_is_shared_with_assamese():
    guess = langid.identify(BENGALI)
    assert guess["language"] is None
    assert guess["script"] == "Bengali"
    assert set(guess["candidates"]) == {"bn", "as"}
    assert guess["confidence"] < 0.6


def test_english_is_decisive():
    guess = langid.identify(ENGLISH)
    assert guess["language"] == "en"
    assert guess["script"] == "Latin"
    assert guess["confidence"] > 0.8
    assert langid.is_english(ENGLISH) is True


def test_mixed_text_is_honest():
    guess = langid.identify(MIXED)
    assert guess["script"] == "Mixed"
    assert guess["language"] is None
    assert guess["confidence"] < 0.5
    assert langid.is_english(MIXED) is False
    assert langid.has_indic_script(MIXED) is True


def test_script_helpers():
    assert langid.has_indic_script(TAMIL) is True
    assert langid.has_indic_script(ENGLISH) is False
    assert langid.dominant_script(TAMIL) == "Tamil"
    assert langid.script_of_text("") == "None"
    assert langid.is_english("") is False


def test_identify_accepts_a_path(tmp_path):
    path = tmp_path / "chapter.txt"
    path.write_text(TAMIL, encoding="utf-8")
    guess = langid.identify(path)
    assert guess["language"] == "ta"
    assert guess["chars_sampled"] == len(TAMIL)


def test_identify_missing_path(tmp_path):
    with pytest.raises(BodhanError) as err:
        langid.identify(str(tmp_path / "absent.txt"))
    assert err.value.code == "MISSING_FILE"


def test_empty_input():
    guess = langid.identify("")
    assert guess == {
        "language": None,
        "script": "None",
        "confidence": 0.0,
        "candidates": [],
        "chars_sampled": 0,
    }
