import pytest

from bodhan_mcp import languages as L


@pytest.mark.parametrize("text,lang", [
    ("வணக்கம் உலகம்", "ta"), ("नमस्ते दुनिया", "hi"), ("হ্যালো বিশ্ব", "bn"),
    ("অসমৰ ৰাজধানী", "as"),  # ৰ only exists in Assamese
    ("సాధారణ వాక్యం", "te"), ("سلام دنیا", "ur"), ("Hello world", "en"),
    ("Chapter 3: नमस्ते दुनिया आज", "hi"), ("123 !!", None),
])
def test_detect_script(text, lang):
    assert L.detect_script(text)[0] == lang


def test_shared_script_is_ambiguous():
    assert "mr" in L.detect_script("नमस्ते")[1]


@pytest.mark.parametrize("value,code", [
    ("Tamil", "ta"), ("TA", "ta"), ("ta-IN", "ta"), ("hindi ", "hi"), ("Oriya", "or"),
    ("ks-deva", "ks-Deva"), ("Kashmiri", "ks"), ("English", "en"), ("French", None), ("fr", None), (None, None),
])
def test_normalize_lang(value, code):
    assert L.normalize_lang(value) == code


def test_tables():
    assert "fr" not in L.TRANSLATE_LANGS and "bho" in L.STT_LANGS and "bho" not in L.TRANSLATE_LANGS
    assert L.pick_voice("bn") == "Ishita" and L.pick_voice("ta", "male") == "Arun"
