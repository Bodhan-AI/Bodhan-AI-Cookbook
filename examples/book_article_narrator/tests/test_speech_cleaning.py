from narrator.pipeline import plan_chunks
from narrator.text_utils import clean_for_speech


def test_emoji_are_removed():
    assert clean_for_speech("बड़ी खबर 🎉 आज। देखें 😊") == "बड़ी खबर आज। देखें"


def test_web_links_are_removed():
    text = "परिणाम आज https://example.com/results पर ऑनलाइन हैं।"
    assert clean_for_speech(text) == "परिणाम आज पर ऑनलाइन हैं।"


def test_a_full_stop_after_a_link_is_kept():
    assert clean_for_speech("See https://example.com/a.") == "See."


def test_indian_script_letters_and_rupee_sign_are_untouched():
    text = "क\u200dष और \u20b91,250 और \u0b95\u0bcd\u0bb7"
    assert clean_for_speech(text) == text


def test_joined_emoji_sequences_are_removed_completely():
    assert clean_for_speech("Hello \U0001F468\u200d\U0001F469\u200d\U0001F467 there") == "Hello there"


def test_plan_chunks_skips_emoji_and_links():
    chunks, _ = plan_chunks(["बड़ी खबर 🎉 देखें https://example.com/x पर।"])
    assert chunks == ["बड़ी खबर देखें पर।"]


def test_a_paragraph_of_only_emoji_gives_no_chunks():
    assert plan_chunks(["🎉😊"]) == ([], [])