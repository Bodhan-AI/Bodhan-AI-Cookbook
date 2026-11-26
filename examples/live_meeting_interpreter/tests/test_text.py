from app.pipeline.text import is_meaningful, split_for_tts


def test_is_meaningful():
    assert not is_meaningful("")
    assert not is_meaningful(" ... ")
    assert is_meaningful("नमस्ते")
    assert is_meaningful("ok")


def test_split_on_danda_and_full_stop():
    text = "यह पहला वाक्य है। यह दूसरा वाक्य है। And a third one."
    parts = split_for_tts(text, max_chars=30)
    assert parts[0].endswith("।")
    assert all(len(p) <= 30 for p in parts)
    assert " ".join(parts).replace("  ", " ") == text


def test_long_sentence_is_broken():
    text = ", ".join(["word " * 8] * 6)
    parts = split_for_tts(text, max_chars=60)
    assert len(parts) > 1 and all(len(p) <= 60 for p in parts)


def test_tidy_removes_space_before_punctuation():
    from app.pipeline.text import tidy
    assert tidy("Got a chance to take blessings of Pathri Mata .") == "Got a chance to take blessings of Pathri Mata."
    assert tidy("Is there such a temple ?") == "Is there such a temple?"
    assert tidy("one , two ; three !") == "one, two; three!"
    assert tidy("यह मंदिर है ।") == "यह मंदिर है।"
    assert tidy("a ( b ) 50 %") == "a (b) 50%"
    assert tidy("Already fine. Really?") == "Already fine. Really?"


def test_user_facing_labels():
    from app.bodhan.languages import TRANSLATE_LANGUAGES, language_name
    from app.hotkey import key_label
    assert key_label("ctrl_r") == "Right Ctrl"
    assert key_label("alt_l") == "Left Alt"
    assert key_label("f9") == "F9"
    assert key_label("scroll_lock") == "Scroll Lock"
    assert key_label("x") == "X"
    assert language_name("hi") == "Hindi" and language_name("en") == "English"
    assert all(language_name(code) != code for code in TRANSLATE_LANGUAGES)


def test_split_for_streaming_keeps_first_chunk_short_and_breaks_naturally():
    from app.pipeline.text import split_for_streaming
    text = "आज मैंने रात का खाना नहीं खाया क्योंकि मेरी बहन ने मेरा खाना खा लिया।"
    parts = split_for_streaming(text)
    assert parts == ["आज मैंने रात का खाना नहीं खाया", "क्योंकि मेरी बहन ने मेरा खाना खा लिया।"]
    assert split_for_streaming("साद मैं जानना चाहता हूँ कि तुम कल छुट्टी क्यों ले रहे हो")[1].startswith("कि ")
    assert split_for_streaming("मैं एक बूढ़ा आदमी हूँ, इसलिए मुझे बोलने में समय लगता है।")[0].endswith(",")


def test_split_for_streaming_short_text_and_word_preservation():
    from app.pipeline.text import split_for_streaming
    assert split_for_streaming("नमस्ते सर") == ["नमस्ते सर"]
    assert split_for_streaming("") == []
    text = " ".join(["शब्द"] * 60)  # no natural breaks at all
    parts = split_for_streaming(text)
    assert len(parts[0]) <= 60  # first chunk hard-capped at 1.5x its budget
    for i in range(1, len(parts)):  # each later chunk is ready before the earlier audio runs out
        assert len(parts[i]) <= max(40, sum(len(p) for p in parts[:i]))
    assert " ".join(parts) == text


def test_split_for_streaming_does_not_leave_a_lone_word():
    from app.pipeline.text import split_for_streaming
    parts = split_for_streaming("मुझे पता है कि आप कल आएंगे और फिर हम मिलकर बात करेंगे तो")
    assert all(len(p) >= 12 for p in parts)


def test_split_for_streaming_short_first_chunk_limits_the_second():
    from app.pipeline.text import split_for_streaming
    # measured on the live API: 18 then 62 chars left a 2.45 s gap between the two parts
    parts = split_for_streaming("लेकिन मुझे पता चला कि वर्क फ्रॉम होम का बहाना बनाकर आप मौज-मस्ती करने जा रहे हैं।")
    assert parts[0] == "लेकिन मुझे पता चला"
    assert len(parts[1]) <= 40
