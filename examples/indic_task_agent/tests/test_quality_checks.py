from bodhan_mcp.checks import check_translation, compare_speech, is_placeholder_text, normalize_for_compare


def test_normalize_drops_punctuation_and_zero_width():
    assert normalize_for_compare("नमस्ते,  दुनिया।") == "नमस्ते दुनिया"
    assert normalize_for_compare("Hello\u200b, WORLD!") == "hello world"


def test_identical_text_passes_despite_punctuation():
    result = compare_speech("नमस्ते, आप कैसे हैं?", "नमस्ते आप कैसे हैं")
    assert result["ok"] and result["ratio"] > 0.95


def test_omission_is_flagged():
    expected = "the quick brown fox jumps over the lazy dog near the river bank today"
    result = compare_speech(expected, "the quick brown fox")
    assert not result["ok"]
    assert any("omitted" in issue for issue in result["issues"])


def test_repetition_is_flagged():
    expected = "the quick brown fox jumps"
    result = compare_speech(expected, expected + " " + expected + " " + expected)
    assert not result["ok"]
    assert any("repeated" in issue for issue in result["issues"])


def test_empty_expected():
    assert compare_speech("", "anything")["ok"] is False


def test_translation_ok():
    result = check_translation("The school is closed.", "शाळा बंद आहे.", "mr")
    assert result["ok"], result


def test_translation_truncated_empty_and_wrong_script():
    assert not check_translation("Hi there friend", "வணக்கம் நண்பா", "ta", truncated=True)["ok"]
    assert not check_translation("Hi", "   ", "ta")["ok"]
    wrong = check_translation("Hi there friend", "hi there friend", "ta")
    assert not wrong["ok"] and "script" in wrong["issues"][0]


def test_translation_placeholder_is_rejected():
    result = check_translation(
        "नमस्ते",
        "The transcript text will be provided after transcription.",
        "en",
    )
    assert not result["ok"]
    assert any("placeholder" in issue for issue in result["issues"])
    assert is_placeholder_text("source text will be provided after transcription")
    assert not is_placeholder_text("The source text will be provided to the class tomorrow.")


def test_translation_roman_script_is_accepted_when_requested():
    result = check_translation(
        "The annual science fair will be held on Friday.",
        "Varshik vigyan mela shukravari hoil.",
        "mr",
        target_script="roman",
    )
    assert result["ok"], result
