import json

import pytest
from conftest import FakeResponse, make_wav
from feedback_analyser.analyse import LLMAnalyser, needs_reply, worst_theme
from feedback_analyser.audio import join_wavs, split_wav, wav_seconds
from feedback_analyser.evaluate import evaluate
from feedback_analyser.ingest import mask_pii, read_manifest
from feedback_analyser.languages import dominant_script, language_for_script
from feedback_analyser.respond import fidelity, speech_chunks


@pytest.mark.parametrize(
    ("text", "script", "language"),
    [
        ("खाना ठंडा था", "Devanagari", "hi"),
        ("जेवण खूप छान होते", "Devanagari", "mr"),
        ("ডেলিভারি দেরি হয়েছে", "Bengali", "bn"),
        ("মই ভাল পাইছোঁ, কিন্তু দাম বেছি আৰু", "Bengali", "as"),
        ("சாப்பாடு நன்றாக இருந்தது", "Tamil", "ta"),
        ("آپ کی سروس خراب ہے", "Arabic", "ur"),
        ("Order तो time पर आया लेकिन packing बहुत खराब थी", "Devanagari", "hi"),
    ],
)
def test_script_and_language_detection(text, script, language):
    assert dominant_script(text) == script
    assert language_for_script(script, text) == language


def test_latin_text_has_no_script_language_and_symbols_have_no_script():
    assert dominant_script("delivery bahut late thi") == "Latin"
    assert language_for_script("Latin", "delivery bahut late thi") is None
    assert dominant_script("12345 !! 🙂") is None


def test_aspects_are_scored_per_clause(analyser):
    result = analyser.analyse("The food was delicious but the delivery was two hours late.")
    aspects = {aspect["theme"]: aspect["sentiment"] for aspect in result["aspects"]}
    assert aspects == {"quality": "positive", "delivery": "negative"}
    assert needs_reply(result) and worst_theme(result) == "delivery"


def test_domain_words_carry_sentiment_that_plain_vader_misses(analyser):
    assert analyser.analyse("The milk was stale and the packet was leaking.")["sentiment"] == "negative"
    assert analyser.analyse("The order arrived on time.")["sentiment"] == "positive"


def test_keywords_match_whole_words_unless_starred(analyser):
    assert analyser.themes_in("I appreciate the apple you sent") == []
    assert analyser.themes_in("The app is fine") == ["app"]
    assert analyser.themes_in("It was delivered yesterday") == ["delivery"]


def test_neutral_question_gets_no_reply(analyser):
    result = analyser.analyse("What time does the shop open in the morning?")
    assert result["sentiment"] == "neutral" and not needs_reply(result) and worst_theme(result) is None


def llm_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "placeholder")
    monkeypatch.setenv("LLM_MODEL", "some-model")


def test_llm_analyser_accepts_valid_json_and_drops_unknown_themes(monkeypatch, taxonomy, analyser):
    llm_env(monkeypatch)
    content = 'Sure: {"sentiment": "negative", "score": -3, "aspects": [{"theme": "delivery", "sentiment": "negative"}, {"theme": "weather", "sentiment": "negative"}]}'
    post = lambda url, **kw: FakeResponse(payload={"choices": [{"message": {"content": content}}]})  # noqa: E731
    FakeResponse.raise_for_status = lambda self: None
    result = LLMAnalyser(taxonomy, analyser, post=post).analyse("Late again.")
    assert result == {"sentiment": "negative", "score": -1.0, "aspects": [{"theme": "delivery", "sentiment": "negative", "score": None}], "analyser": "llm"}
    assert worst_theme(result) == "delivery"


def test_llm_analyser_falls_back_to_lexicon_on_unusable_reply(monkeypatch, taxonomy, analyser):
    llm_env(monkeypatch)
    post = lambda url, **kw: FakeResponse(payload={"choices": [{"message": {"content": "I think it is bad"}}]})  # noqa: E731
    FakeResponse.raise_for_status = lambda self: None
    result = LLMAnalyser(taxonomy, analyser, post=post).analyse("The delivery was late.")
    assert result["sentiment"] == "negative" and result["analyser"].startswith("lexicon")


def test_llm_analyser_requires_its_environment(monkeypatch, taxonomy, analyser):
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        LLMAnalyser(taxonomy, analyser)


def test_fidelity_is_high_for_paraphrase_and_low_for_unrelated_text():
    reference = "We are sorry that your order did not reach you on time."
    assert fidelity(reference, "We are sorry your order did not reach you on time.") > 0.9
    assert fidelity(reference, "We apologise that your order was not delivered on time.") >= 0.5
    assert fidelity(reference, "The weather is nice today.") == 0.0
    assert fidelity(reference, "") == 0.0


def test_speech_chunks_keep_sentences_whole_and_under_the_limit():
    text = "पहला वाक्य यहाँ है। दूसरा वाक्य यहाँ है। तीसरा वाक्य यहाँ है।"
    chunks = speech_chunks(text, limit=40)
    assert chunks == ["पहला वाक्य यहाँ है। दूसरा वाक्य यहाँ है।", "तीसरा वाक्य यहाँ है।"]
    assert speech_chunks("  ") == []


def test_wav_split_and_join_round_trip():
    clip = make_wav(60)
    parts = split_wav(clip, 25)
    assert [round(wav_seconds(part)) for part in parts] == [25, 25, 10]
    assert wav_seconds(join_wavs(parts)) == pytest.approx(60)
    with pytest.raises(ValueError):
        join_wavs([make_wav(1, rate=8000), make_wav(1, rate=16000)])


def test_manifest_rows_are_validated(tmp_path):
    manifest = tmp_path / "inbox.csv"
    manifest.write_text("id,channel,text,file,language\na,text,नमस्ते,,hi\nb,voice,,clips/b.wav,\n", encoding="utf-8")
    first, second = read_manifest(manifest)
    assert first.language == "hi" and second.file == tmp_path / "clips/b.wav" and second.language is None
    for bad in ("a,text,,,\n", "a,fax,hello,,\n", "a,card,,,\n", "a,text,x,,\na,text,y,,\n"):
        manifest.write_text("id,channel,text,file,language\n" + bad, encoding="utf-8")
        with pytest.raises(ValueError):
            read_manifest(manifest)


def test_pii_masking():
    assert mask_pii("call 98765 43210 or +91-9876543210, mail a.b@shop.in, order 4521") == "call [phone] or [phone], mail [email], order 4521"


def test_evaluate_counts_language_sentiment_and_themes():
    records = [
        {"id": "a", "error": None, "language": "hi", "sentiment": "negative", "themes": ["delivery", "price"], "english": "x"},
        {"id": "b", "error": None, "language": "hi", "sentiment": "positive", "themes": [], "english": "y"},
        {"id": "c", "error": "boom", "language": None, "sentiment": None, "themes": [], "english": None},
        {"id": "unlabelled", "error": None, "language": "ta", "sentiment": "neutral", "themes": [], "english": "z"},
    ]
    expected = {
        "a": {"language": "hi", "sentiment": "negative", "themes": {"delivery"}},
        "b": {"language": "mr", "sentiment": "positive", "themes": {"staff"}},
        "c": {"language": "ta", "sentiment": "negative", "themes": {"app"}},
    }
    scores = evaluate(records, expected)
    assert scores["items"] == 3 and scores["failed"] == 1
    assert scores["language_accuracy"] == pytest.approx(1 / 3, abs=0.001)
    assert scores["sentiment_accuracy"] == pytest.approx(2 / 3, abs=0.001)
    assert scores["theme_precision"] == 0.5 and scores["theme_recall"] == pytest.approx(1 / 3, abs=0.001)
    assert [miss["id"] for miss in scores["misses"]] == ["a", "b", "c"]
    assert json.dumps(scores)
