"""Unit tests for language and script detection, with special focus on
Devanagari disambiguation between Marathi (mr) and Hindi (hi).
"""

import pytest
from bodhan_mcp.tools.lang_detect import detect_text_language


MARATHI_TEST_SENTENCES = [
    "नमस्कार मित्रांनो, कसे आहात?",
    "घराच्या समोर एक खूप मोठे झाड आहे.",
    "आज संध्याकाळी पाऊस पडणार नाही.",
    "आम्ही सर्वजण उद्या पुण्याला जाणार आहोत.",
    "तो मुलगा वेगाने पळत होता.",
    "मला या योजनेची संपूर्ण माहिती पाहिजे.",
]

HINDI_TEST_SENTENCES = [
    "नमस्ते दोस्तों, आप सब कैसे हैं?",
    "यह किताब मेरी मेज पर रखी है.",
    "हम कल सुबह दिल्ली के लिए निकल रहे हैं.",
    "उसने मुझे अपनी नई गाड़ी के बारे में बताया.",
    "वहाँ बहुत सारे बच्चे खेल रहे थे.",
    "क्या आपको यह बात मालूम नहीं थी?",
]

AMBIGUOUS_SHORT_TEXTS = [
    "नमस्कार",
    "भारत",
    "शांती",
    "धन्यवाद",
]


@pytest.mark.parametrize("sentence", MARATHI_TEST_SENTENCES)
def test_marathi_disambiguation(sentence: str):
    """Verify Marathi sentences are identified as 'mr' with high confidence (>0.75) and not assumed."""
    result = detect_text_language(sentence)
    assert result.language == "mr", f"Expected 'mr' for '{sentence}', got '{result.language}'"
    assert result.script == "Devanagari"
    assert result.confidence >= 0.75, f"Expected confidence >= 0.75, got {result.confidence}"
    assert result.is_assumed is False, "Expected is_assumed=False for distinctive Marathi sentence"


@pytest.mark.parametrize("sentence", HINDI_TEST_SENTENCES)
def test_hindi_disambiguation(sentence: str):
    """Verify Hindi sentences are identified as 'hi' with high confidence (>0.75) and not assumed."""
    result = detect_text_language(sentence)
    assert result.language == "hi", f"Expected 'hi' for '{sentence}', got '{result.language}'"
    assert result.script == "Devanagari"
    assert result.confidence >= 0.75, f"Expected confidence >= 0.75, got {result.confidence}"
    assert result.is_assumed is False, "Expected is_assumed=False for distinctive Hindi sentence"


@pytest.mark.parametrize("text", AMBIGUOUS_SHORT_TEXTS)
def test_ambiguous_short_devanagari_text(text: str):
    """Verify very short/ambiguous Tatsama single-word text maintains low confidence and is_assumed=True."""
    result = detect_text_language(text)
    assert result.language == "hi"  # Default fallback
    assert result.script == "Devanagari"
    assert result.confidence == 0.50
    assert result.is_assumed is True


def test_unique_marathi_character_l():
    """Verify presence of 'ळ' (\u0933) guarantees 0.99 confidence Marathi."""
    res = detect_text_language("खेळ")
    assert res.language == "mr"
    assert res.confidence == 0.99
    assert res.is_assumed is False


def test_target_phrase_namaskar_mitrano():
    """Verify target phrase 'नमस्कार मित्रांनो' returns Marathi with high confidence."""
    res = detect_text_language("नमस्कार मित्रांनो")
    assert res.language == "mr"
    assert res.confidence >= 0.85
    assert res.is_assumed is False

