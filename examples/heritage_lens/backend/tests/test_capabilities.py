import pytest

from app.services.bodhan import capabilities as caps
from app.services.bodhan.exceptions import BodhanRequestInvalid


def test_each_service_has_its_own_language_list():
    assert "bhb" in caps.TRANSCRIBE_LANGUAGES and "bho" in caps.TRANSCRIBE_LANGUAGES
    assert "bhb" not in caps.TRANSLATE_TARGETS and "bho" not in caps.TRANSLATE_TARGETS
    assert "en" in caps.TRANSLATE_TARGETS
    assert "en" not in caps.TRANSLITERATE_LANGUAGES
    assert "mai" not in caps.SPEAK_LANGUAGES or True  # mai is a speak language per docs
    assert "mai" in caps.SPEAK_LANGUAGES
    assert len(caps.TRANSLATE_TARGETS) == 26
    assert len(caps.SPEAK_VOICES) == 45


def test_telugu_voices_present():
    assert caps.SPEAK_VOICES["Sravani"] == ("female", "te")
    assert caps.SPEAK_VOICES["Vamsi"] == ("male", "te")


def test_bhili_bhojpuri_rejected_for_translation():
    for code in ("bhb", "bho"):
        with pytest.raises(BodhanRequestInvalid, match="not supported"):
            caps.ensure_translate_target(code)


def test_unknown_language_rejected():
    with pytest.raises(BodhanRequestInvalid):
        caps.ensure_translate_target("xx")
    with pytest.raises(BodhanRequestInvalid):
        caps.ensure_transcribe_language("xx")
    with pytest.raises(BodhanRequestInvalid):
        caps.ensure_speak_language("bho")


def test_translate_script_rules():
    assert caps.ensure_translate_script("te", "roman") == "roman"
    with pytest.raises(BodhanRequestInvalid):
        caps.ensure_translate_script("en", "roman")
    with pytest.raises(BodhanRequestInvalid):
        caps.ensure_translate_script("sd-Arab", "codemix")
    with pytest.raises(BodhanRequestInvalid):
        caps.ensure_translate_script("te", "latin")


def test_transliteration_rules():
    with pytest.raises(BodhanRequestInvalid, match="English"):
        caps.ensure_transliterate_language("en")
    with pytest.raises(BodhanRequestInvalid):
        caps.ensure_transliterate_script("codemix")
    assert caps.ensure_transliterate_language("te") == "te"


def test_voice_and_style_canonicalisation():
    assert caps.canonical_voice("sravani") == "Sravani"
    with pytest.raises(BodhanRequestInvalid):
        caps.canonical_voice("Nobody")
    assert caps.canonical_style("NEWS") == "news"
    assert caps.canonical_style(None) is None
    with pytest.raises(BodhanRequestInvalid):
        caps.canonical_style("sarcastic")


def test_capabilities_payload_shape():
    payload = caps.build_capabilities({"translate": True}, ffmpeg_available=True)
    assert payload["services"]["indic-translate"]["configured"] is True
    assert payload["services"]["indic-speak"]["configured"] is False
    assert "bhb" in payload["services"]["indic-translate"]["unsupported_languages"]
    assert payload["services"]["indic-ocr"]["upstream_formats"] == ["png", "jpeg"]
