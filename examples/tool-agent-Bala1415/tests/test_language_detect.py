"""Unit tests for language detection helper."""

import pytest

from bodhan_mcp.helpers.detect_language import (
    detect_language,
    detect_language_code,
    get_language_name,
    LANGUAGE_NAMES,
)


@pytest.mark.unit
class TestDetectLanguage:
    def test_hindi_text(self):
        result = detect_language("नमस्ते, यह एक परीक्षण है।")
        assert result["language_code"] == "hi"
        assert result["script"] == "DEVANAGARI"
        assert result["confidence"] > 0.5

    def test_tamil_text(self):
        result = detect_language("வணக்கம், இது ஒரு சோதனை.")
        assert result["language_code"] == "ta"
        assert result["script"] == "TAMIL"
        assert result["confidence"] > 0.5

    def test_telugu_text(self):
        result = detect_language("నమస్కారం, ఇది ఒక పరీక్ష.")
        assert result["language_code"] == "te"
        assert result["script"] == "TELUGU"

    def test_bengali_text(self):
        result = detect_language("নমস্কার, এটি একটি পরীক্ষা।")
        assert result["language_code"] == "bn"
        assert result["script"] == "BENGALI"

    def test_malayalam_text(self):
        result = detect_language("നമസ്കാരം, ഇത് ഒരു പരീക്ഷണമാണ്.")
        assert result["language_code"] == "ml"
        assert result["script"] == "MALAYALAM"

    def test_kannada_text(self):
        result = detect_language("ನಮಸ್ಕಾರ, ಇದು ಒಂದು ಪರೀಕ್ಷೆ.")
        assert result["language_code"] == "kn"
        assert result["script"] == "KANNADA"

    def test_gujarati_text(self):
        result = detect_language("નમસ્તે, આ એક પરીક્ષણ છે.")
        assert result["language_code"] == "gu"
        assert result["script"] == "GUJARATI"

    def test_english_text(self):
        result = detect_language("Hello, this is a test.")
        assert result["language_code"] == "en"
        assert result["script"] == "LATIN"

    def test_marathi_text(self):
        # Use more Marathi-specific words to help disambiguation
        result = detect_language("नमस्कार, हे एक परीक्षण आहे. ती आणि तो नाही आहेत.")
        assert result["language_code"] == "mr"
        assert result["script"] == "DEVANAGARI"

    def test_empty_text(self):
        result = detect_language("")
        assert result["language_code"] == "unknown"
        assert result["confidence"] == 0.0

    def test_whitespace_only(self):
        result = detect_language("   \n\t  ")
        assert result["language_code"] == "unknown"

    def test_mixed_script(self):
        """Mixed English-Hindi text should detect dominant script."""
        result = detect_language("Hello नमस्ते World यह है")
        assert result["language_code"] in ("hi", "en")
        assert result["confidence"] > 0.0
        assert "script_distribution" in result

    def test_numbers_and_punctuation(self):
        """Numbers and punctuation should not affect detection."""
        result = detect_language("123, 456! ???")
        # May return unknown since there are no actual script characters
        assert "language_code" in result


@pytest.mark.unit
class TestLanguageHelpers:
    def test_detect_language_code(self):
        code = detect_language_code("நமஸ்காரம்")
        assert code == "ta"

    def test_get_language_name(self):
        assert get_language_name("hi") == "Hindi"
        assert get_language_name("ta") == "Tamil"
        assert get_language_name("bn") == "Bengali"
        assert "Unknown" in get_language_name("xx")

    def test_language_names_complete(self):
        """Ensure all major Indic languages have names."""
        for code in ["hi", "bn", "ta", "te", "mr", "gu", "kn", "ml", "pa", "or", "as", "ur"]:
            assert code in LANGUAGE_NAMES
