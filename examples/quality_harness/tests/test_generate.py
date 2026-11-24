import copy
import json
import wave
from pathlib import Path

import pytest

from bodhan_eval.dataset import Item
from bodhan_eval.generate import audio_cost, audio_name, generate, join_wavs, select_for_audio
from bodhan_eval.segment import chunks, speakable, split_sentences
from test_clients import TRANSLATE_OK, make, silent_wav

SPEECH = (200, silent_wav(2.0), {"content-type": "audio/mpeg"})


def items(tmp_path):
    supplied_wav = tmp_path / "given.wav"
    supplied_wav.write_bytes(silent_wav(1.0))
    return [
        Item("a", "hi", "Water boils at 100 °C.", 1),  # nothing supplied
        Item("b", "ta", "One. Two.", 2, translation="ஒன்று. இரண்டு."),  # translation only
        Item("c", "bn", "Hi.", 3, translation="হাই।", audio=supplied_wav),  # everything supplied
        Item("d", "bn", "Code.", 4, translation="```\nprint(1)\n```"),  # nothing speakable
    ]


def test_fills_only_what_is_missing(tmp_path):
    clients, server = make(tmp_path, {"/v1/chat/completions": [TRANSLATE_OK], "/v1/audio/speech": [SPEECH] * 4})
    data = items(tmp_path)
    stats = generate(data, clients, tmp_path / "out" / "audio", None)
    a, b, c, d = data

    assert a.translation.startswith("समुद्र") and {k: a.status[k] for k in ("translation", "audio")} == {"translation": "generated", "audio": "generated"}
    assert {k: b.status[k] for k in ("translation", "audio")} == {"translation": "supplied", "audio": "generated"}
    assert {a.status["audio_sample"], b.status["audio_sample"]} <= {"risk", "random"}
    assert c.status == {"translation": "supplied", "audio": "supplied"} and c.audio.name == "given.wav"
    assert d.status["audio"] == "empty"
    assert stats == {"translation": {"generated": 1, "supplied": 3}, "audio": {"empty": 1, "generated": 2, "supplied": 1}}

    # b: one request per sentence, the Tamil voice, base language code, and a timing per sentence
    assert [s["text"] for s in b.audio_segments] == ["ஒன்று.", "இரண்டு."]
    assert [(s["start_s"], s["end_s"]) for s in b.audio_segments] == [(0.0, 2.0), (2.0, 4.0)]
    with wave.open(str(b.audio)) as w:
        assert w.getnframes() / w.getframerate() == 4.0
    speak_bodies = [json.loads(r.content) for r in server.requests if r.url.path.endswith("speech")]
    assert {(body["voice"], body["input"]) for body in speak_bodies} >= {("Anitha", "ஒன்று."), ("Anitha", "இரண்டு.")}


def test_rerun_is_served_from_cache_and_identical(tmp_path):
    routes = {"/v1/chat/completions": [TRANSLATE_OK], "/v1/audio/speech": [SPEECH] * 4}
    clients, _ = make(tmp_path, routes)
    first = items(tmp_path)
    generate(first, clients, tmp_path / "r1", None)
    clients.close()

    clients, server = make(tmp_path, {})  # same cache, no responses: any live call would fail
    second = items(tmp_path)
    generate(second, clients, tmp_path / "r2", None)
    assert server.requests == []
    for x, y in zip(first, second):
        assert (x.translation, x.status, x.audio_segments) == (y.translation, y.status, y.audio_segments)
    assert (tmp_path / "r1" / "b.ta.wav").read_bytes() == (tmp_path / "r2" / "b.ta.wav").read_bytes()


def test_failures_and_cut_off_output_are_recorded(tmp_path):
    cut = copy.deepcopy(TRANSLATE_OK)
    cut[1]["choices"][0]["finish_reason"] = "length"
    clients, _ = make(tmp_path, {"/v1/chat/completions": [cut], "/v1/audio/speech": [(400, {"error": {"message": "bad"}}, {})]})
    item = Item("a", "hi", "Text.", 1)
    generate([item], clients, tmp_path / "audio", None)
    assert item.status["translation"] == "generated" and item.status["audio"] == "error"
    assert "cut off" in item.warnings[0] and "speech generation failed" in item.warnings[1]

    clients, _ = make(tmp_path / "2", {"/v1/chat/completions": [(400, {"error": {"message": "bad"}}, {})]})
    item = Item("a", "hi", "Text.", 1)
    generate([item], clients, tmp_path / "audio", None)
    assert item.status == {"translation": "error", "audio": "missing"} and item.translation is None


def test_audio_selection_is_seeded_interleaved_and_budgeted():
    pool = [Item(f"{lang}{n}", lang, "x", 0, translation="एक वाक्य।" * 5) for lang in ("hi", "ta", "bn") for n in range(4)]
    everything, _ = select_for_audio(pool, None)
    assert len(everything) == 12
    assert select_for_audio(list(reversed(pool)), None)[0] == everything  # input order doesn't matter
    assert {i.lang for i in everything[:3]} == {"hi", "ta", "bn"}  # languages alternate

    budget = audio_cost(pool[0].translation) * 4.5
    some, cost = select_for_audio(pool, budget)
    assert some == everything[:4] and cost <= budget


def test_join_wavs_rejects_mixed_formats(tmp_path):
    (tmp_path / "a.wav").write_bytes(silent_wav(1.0, rate=24_000))
    (tmp_path / "b.wav").write_bytes(silent_wav(1.0, rate=16_000))
    with pytest.raises(ValueError, match="differs"):
        join_wavs([tmp_path / "a.wav", tmp_path / "b.wav"], tmp_path / "out.wav")


def test_audio_names_are_safe_and_distinct():
    assert audio_name(Item("sci-042", "hi", "x", 0)) == "sci-042.hi.wav"
    assert audio_name(Item("a/b", "hi", "x", 0)) != audio_name(Item("a_b", "hi", "x", 0))
    assert "/" not in audio_name(Item("../../etc", "hi", "x", 0))


def test_split_sentences():
    text = "  पानी 3.5 लीटर है। क्या?  Yes!\nNext line\n\n"
    assert [text[s:e] for s, e in split_sentences(text)] == ["पानी 3.5 लीटर है।", "क्या?", "Yes!", "Next line"]
    assert split_sentences("   ") == []


def test_speakable_strips_markup():
    md = (
        "## परिणाम\n\nयह **महत्वपूर्ण** है, [लिंक](https://x.org) देखें।\n\n"
        "| नाम | मान |\n|---|---|\n| क | 1 |\n\n"
        "```python\nprint(1)\n```\n"
        "ऊर्जा $E = mc^2$ है। देखें https://example.org <br>\n- सूची"
    )
    assert speakable(md) == "परिणाम\nयह महत्वपूर्ण है, लिंक देखें।\nनाम, मान\nक, 1\nऊर्जा है। देखें\nसूची"
    assert speakable("snake_case and 2*3") == "snake_case and 2*3"  # not emphasis


def test_long_sentences_are_cut_under_the_limit():
    long = "शब्द, " * 100 + "अंत।"
    parts = chunks(long, 50)
    assert all(len(p) <= 50 for p in parts) and "".join(parts).replace(" ", "") == long.replace(" ", "")


def test_speech_sample_is_risky_and_random_alternately_and_capped():
    from bodhan_eval.dataset import Item
    from bodhan_eval.generate import risk, select_for_audio

    plain = [Item(id=f"p{k}", lang="hi", source="x", line=k, translation="एक सीधा वाक्य है।") for k in range(6)]
    risky = Item(id="r", lang="hi", source="x", line=9, translation="ISRO ने 1969 में (बेंगलुरु) काम शुरू किया।")
    assert risk(risky.translation) >= 3 and risk(plain[0].translation) == 0
    chosen, _ = select_for_audio(plain + [risky], None, max_items=3)
    assert len(chosen) == 3 and chosen[0] is risky and {i.status["audio_sample"] for i in chosen} == {"risk", "random"}
    assert all("audio_sample" not in i.status for i in plain + [risky] if i not in chosen)
