"""Unit tests for script identification and language detection."""
from __future__ import annotations

import pytest
from bodhan_mcp.tools.media import detect_script


def test_detect_devanagari():
    """Devanagari text detects script Devanagari and language hi."""
    text = "नमस्ते, आप कैसे हैं? यह हिन्दी में लिखा गया वाक्य है।"
    res = detect_script(text)
    assert res.ok is True
    assert res.script_name.lower() == "devanagari"
    assert res.detected_language in {"hi", "mr", "sa"}


def test_detect_tamil():
    """Tamil script text detects Tamil and ta."""
    text = "வணக்கம், நீங்கள் எப்படி இருக்கிறீர்கள்? தமிழ் மிகவும் அழகான மொழி."
    res = detect_script(text)
    assert res.ok is True
    assert res.script_name.lower() == "tamil"
    assert res.detected_language == "ta"


def test_detect_bengali():
    """Bengali script text detects Bengali and bn."""
    text = "নমস্কার, আপনি কেমন আছেন? বাংলা একটি সুন্দর ভাষা।"
    res = detect_script(text)
    assert res.ok is True
    assert res.script_name.lower() == "bengali"
    assert res.detected_language == "bn"


def test_detect_telugu():
    """Telugu script text detects Telugu and te."""
    text = "నమస్కారం, మీరు ఎలా ఉన్నారు? తెలుగు చాలా మధురమైన భాష."
    res = detect_script(text)
    assert res.ok is True
    assert res.script_name.lower() == "telugu"
    assert res.detected_language == "te"


def test_detect_latin():
    """Latin script text detects Latin and en."""
    text = "Hello, this is an English sentence using Latin script."
    res = detect_script(text)
    assert res.ok is True
    assert res.script_name.lower() == "latin"
    assert res.detected_language == "en"


def test_detect_empty_or_whitespace():
    """Empty string returns None script and low confidence."""
    res = detect_script("")
    assert res.ok is True
    assert res.script_name is None
    assert res.detected_language is None

    res_ws = detect_script("   \n\t  ")
    assert res_ws.ok is True
    assert res_ws.script_name is None


def test_detect_mixed_script_majority():
    """Mixed text detects majority script."""
    # Mostly Tamil with one Latin word
    text = "வணக்கம், நீங்கள் எப்படி இருக்கிறீர்கள் hello"
    res = detect_script(text)
    assert res.ok is True
    assert res.script_name.lower() == "tamil"
