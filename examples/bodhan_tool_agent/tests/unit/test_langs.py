import pytest

from bodhan_core import langs
from bodhan_core.errors import ErrorCode, ToolError


@pytest.mark.parametrize("value", ["ta", "Tamil", " tamil ", "TA"])
def test_normalize_language(value):
    assert langs.normalize_language(value) == "ta"


def test_french_is_unsupported():
    with pytest.raises(ToolError) as exc:
        langs.normalize_language("French")
    assert exc.value.info.code is ErrorCode.UNSUPPORTED_LANGUAGE
    assert "Supported" in exc.value.info.hint


def test_model_specific_support():
    assert langs.require_supported("indic-transcribe", "Bhili") == "bhb"
    with pytest.raises(ToolError):
        langs.require_supported("indic-speak", "bhb")  # Speak has no Bhili


def test_female_bengali_voice():
    assert [v["voice"] for v in langs.voices("bn", "female")] == ["Ishita"]


@pytest.mark.parametrize(
    "text,script,candidates",
    [
        ("வணக்கம், இன்று வகுப்பு", "Tamil", ["ta"]),
        ("ఈ రోజు తరగతి", "Telugu", ["te"]),
        ("Hello world", "Latin", ["en"]),
    ],
)
def test_detect_script_unambiguous(text, script, candidates):
    r = langs.detect_script(text)
    assert r["script"] == script and r["candidates"] == candidates and not r["ambiguous"]


def test_devanagari_is_ambiguous():
    r = langs.detect_script("कल विद्यालय बंद रहेगा")
    assert r["script"] == "Devanagari" and r["ambiguous"] and "hi" in r["candidates"] and "mr" in r["candidates"]


def test_empty_text():
    assert langs.detect_script("123 ...")["script"] is None
