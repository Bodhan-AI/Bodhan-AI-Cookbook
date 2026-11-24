"""
Unit tests for language and voice configuration (src/bodhan_core/langs.py).
"""
from bodhan_core.langs import (
    is_supported,
    detect_script,
    pick_voice,
    TRANSCRIBE_LANGS,
    TRANSLATE_LANGS,
    SPEAK_LANGS,
)


def test_transcribe_supported():
    assert is_supported("hi", "transcribe") is True
    assert is_supported("ta", "transcribe") is True
    assert is_supported("fr", "transcribe") is False
    assert is_supported("es", "transcribe") is False


def test_translate_supported():
    assert is_supported("bn", "translate") is True
    assert is_supported("mr", "translate") is True
    assert is_supported("zh", "translate") is False


def test_speak_supported():
    assert is_supported("te", "speak") is True
    assert is_supported("gu", "speak") is True
    assert is_supported("de", "speak") is False


def test_pick_voice():
    v_hi_f = pick_voice("hi", gender="female")
    assert v_hi_f in ["Kavya", "Suhani"]

    v_hi_m = pick_voice("hi", gender="male")
    assert v_hi_m == "Amit"

    v_ta_f = pick_voice("ta", gender="female")
    assert v_ta_f == "Anitha"


def test_detect_script():
    # Devanagari text
    dev_text = "नमस्ते दुनिया"
    dev_langs = detect_script(dev_text)
    assert any(l in dev_langs for l in ["hi", "mr", "sa", "ne"])

    # Tamil text
    ta_text = "வணக்கம் உலகம்"
    ta_langs = detect_script(ta_text)
    assert "ta" in ta_langs

    # Bengali text
    bn_text = "নমস্কার বিশ্ব"
    bn_langs = detect_script(bn_text)
    assert any(l in bn_langs for l in ["bn", "as"])
