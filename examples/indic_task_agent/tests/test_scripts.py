from bodhan_mcp.scripts import detect_script, script_matches_language


def test_tamil_is_unambiguous():
    info = detect_script("வணக்கம் உலகம்")
    assert info["script"] == "Tamil"
    assert info["candidates"] == ["ta"]
    assert info["unambiguous"] is True


def test_telugu_and_not_tamil():
    assert detect_script("నమస్కారం")["script"] == "Telugu"


def test_devanagari_is_ambiguous():
    info = detect_script("नमस्ते दुनिया।")
    assert info["script"] == "Devanagari"
    assert "hi" in info["candidates"] and "mr" in info["candidates"]
    assert info["unambiguous"] is False
    assert info["notes"]


def test_assamese_letter_hint():
    info = detect_script("ৰাজ্য")
    assert info["script"] == "Bengali"
    assert info["candidates"][0] == "as"


def test_empty_and_numbers():
    assert detect_script("")["script"] is None
    assert detect_script("123 456 !!")["script"] is None


def test_mixed_script_flagged():
    info = detect_script("வணக்கம் hello world friends")
    assert info["script"] == "Latin"
    assert info["confidence"] < 1.0


def test_script_matches_language():
    assert script_matches_language("வணக்கம் உலகம்", "ta")
    assert not script_matches_language("hello there", "ta")
    assert script_matches_language("12345", "ta")  # nothing to judge
    assert script_matches_language("anything", "xx")  # unknown language: cannot judge
