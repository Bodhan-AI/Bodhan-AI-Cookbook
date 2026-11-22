import pytest

from bodhan_mcp.languages import check_support, normalize_language, pick_voice


def test_normalize_names_and_codes():
    assert normalize_language("Hindi") == "hi"
    assert normalize_language("hi-IN") == "hi"
    assert normalize_language("KS-deva") == "ks-Deva"
    assert normalize_language("Kashmiri") == "ks"
    assert normalize_language("Oriya") == "or"
    assert normalize_language("French") == "fr"
    assert normalize_language("") is None
    assert normalize_language("12345!") is None


def test_supported_and_unsupported():
    assert check_support("speak", "Tamil")["supported"] is True
    result = check_support("translate", "French")
    assert result["supported"] is False
    assert "fr" == result["language"]
    assert "hi" in result["supported_languages"]


def test_stt_only_language_explains_where_it_works():
    result = check_support("translate", "bho")
    assert result["supported"] is False
    assert "transcribe" in result["reason"]


def test_unknown_capability():
    assert check_support("juggle", "hi")["supported"] is False


def test_pick_voice():
    assert pick_voice("hi", gender="female")["voice"] == "Kavya"
    assert pick_voice("ta", gender="male")["voice"] == "Arun"
    assert pick_voice("ta")["voice"] == "Anitha"  # default female
    assert pick_voice("hi", name="kavya")["voice"] == "Kavya"
    assert pick_voice("en", gender="male")["voice"] == "Amit"  # no English voice: falls back to Hindi's


def test_pick_voice_errors():
    with pytest.raises(ValueError):
        pick_voice("hi", name="NoSuchVoice")
    with pytest.raises(ValueError):
        pick_voice("hi", gender="robot")
