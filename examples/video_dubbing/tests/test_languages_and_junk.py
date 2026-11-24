"""All 13 production-tier languages, and junk ASR replies (a run of one repeated character).

Fixtures are real API outputs from the live probe of 2026-11-20 (one maths sentence translated by
Indic-Translate, spoken by Indic-Speak, transcribed back by Indic-Transcribe). No API calls here."""

import json

import numpy as np
import pytest
import soundfile as sf

from bodhan_dub import align, qa, transcribe
from bodhan_dub import translate as T
from bodhan_dub.align import align_chunks
from bodhan_dub.chunking import Chunk
from bodhan_dub.pipeline import DEFAULT_VOICES, ISO639_2, OTHER_VOICES

SRC = "The area of a triangle is half of the base times the height."
PROBE = {  # Indic-Translate output for SRC, per language
    "hi": "त्रिकोण का क्षेत्रफल आधार और ऊँचाई के गुणनफल का आधा होता है।",
    "ta": "ஒரு முக்கோணத்தின் பரப்பளவு அதன் அடிப்பக்கத்தின் உயரத்தை பாதியாக பெருக்கினால் கிடைப்பது ஆகும்.",
    "bn": "ত্রিভুজের ক্ষেত্রফল হল ভূমির অর্ধেক এবং উচ্চতার গুণফল।",
    "mr": "त्रिकोणाचे क्षेत्रफळ हे पायाच्या उंचीने गुणल्यावर मिळणाऱ्या बेरजेच्या अर्धे असते.",
    "te": "త్రిభుజం యొక్క వైశాల్యం అనేది దాని ఆధారానికి ఎత్తును సగానికి గుణించగా వచ్చేది.",
    "gu": "ત્રિકોણનું ક્ષેત્રફળ એ પાયાની લંબાઈ અને ઊંચાઈના ગુણાકારથી અડધું હોય છે.",
    "kn": "ತ್ರಿಕೋನದ ವಿಸ್ತೀರ್ಣವು ತಳದ ಅರ್ಧದಷ್ಟು ಎತ್ತರವನ್ನು ಗುಣಿಸಿದಾಗ ಬರುತ್ತದೆ.",
    "ml": "ഒരു ത്രികോണത്തിന്‍റെ വിസ്തീർണ്ണം അതിന്‍റെ അടിസ്ഥാനവും ഉയരവും തമ്മിൽ ഗുണിച്ചതിന്‍റെ പകുതിയാണ്.",
    "or": "ତ୍ରିଭୁଜର କ୍ଷେତ୍ରଫଳ ହେଉଛି ଭିତ୍ତି ଓ ଉଚ୍ଚତାର ଗୁଣଫଳର ଅଧା।",
    "pa": "ਇੱਕ ਤਿਕੋਣ ਦਾ ਖੇਤਰਫਲ ਅਧਾਰ ਅਤੇ ਉਚਾਈ ਦੇ ਗੁਣਾ ਦਾ ਅੱਧਾ ਹੁੰਦਾ ਹੈ।",
    "as": "এটা ত্ৰিভুজৰ কালি হৈছে ভূমি আৰু উচ্চতাৰ পূৰণফলৰ আধা।",
    "ur": "تکون کا رقبہ بنیاد اور اونچائی کے نصف ضرب کے برابر ہوتا ہے۔",
}
# Real junk replies (Assamese / Urdu back-transcription), shortened: the run was ~200 characters long.
JUNK_AS = "এটা ত্ৰিভুজৰ কালি হৈছে ভূমি আৰু উচ্চতাৰ পূৰ" + "ୄ" * 40
JUNK_UR = "تکون کا رقبہ بنیاد اور اونچائی کے نصف ضر" + "ୄ" * 40
PRODUCTION = ["en", "hi", "bn", "mr", "te", "ta", "gu", "kn", "ml", "or", "pa", "as", "ur"]


# ---------------------------------------------------------------- languages
def test_all_13_production_languages_are_configured():
    for code in PRODUCTION:
        assert T.lang_name(code) and code in T.SCRIPT_RANGES and code in DEFAULT_VOICES and code in ISO639_2
    assert set(T.LANG_NAMES) == set(PRODUCTION)


def test_each_language_has_two_distinct_voices():
    for code in PRODUCTION:
        if code != "en":
            assert DEFAULT_VOICES[code] != OTHER_VOICES[code]


@pytest.mark.parametrize("code", sorted(PROBE))
def test_real_translation_passes_every_check(code):
    chk = T.check_translation(SRC, PROBE[code], code, "stop")
    assert chk["failures"] == [], chk
    assert T.script_share(PROBE[code], code) > 0.95


def test_wrong_script_is_caught():
    assert T.script_share(PROBE["mr"], "ta") == 0.0
    assert T.script_share(PROBE["ur"], "hi") == 0.0
    assert T.script_share(PROBE["hi"], "ur") == 0.0
    assert T.script_share(SRC, "kn") == 0.0
    # Marathi shares Devanagari with Hindi, Assamese shares the Bengali block: the script check cannot tell
    # them apart (by design; it catches English or wrong-script output, not the wrong language).
    assert T.script_share(PROBE["mr"], "hi") > 0.95 and T.script_share(PROBE["as"], "bn") > 0.95


def test_english_is_passed_through_without_an_api_call(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "post_with_retry", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")))
    r = T.translate_text(" Hello there. ", "en", "k", tmp_path)
    assert r["ok"] and r["text"] == "Hello there." and r["finish"] == "stop"
    units = [{"id": i, "start": i, "end": i + 1, "text": f"Sentence {i}."} for i in range(3)]
    segs = T.translate_units(units, "en", tmp_path)
    assert [s["translation"] for s in segs] == [u["text"] for u in units]
    assert all(s["status"] == "ok" for s in segs)


# ---------------------------------------------------------------- junk detection
def test_junk_is_detected_only_on_runs_of_one_character():
    assert transcribe.is_junk(JUNK_AS) and transcribe.is_junk(JUNK_UR)
    assert not any(transcribe.is_junk(t) for t in PROBE.values())
    assert not transcribe.is_junk("THE AREA OF A TRIANGLE IS HALF OF THE BASE TIMES THE HEIGHT")
    assert not transcribe.is_junk("A" * 7) and transcribe.is_junk("A" * 8)
    assert not transcribe.is_junk("ha ha ha ha ha ha ha ha ha")   # repeated words are not character runs


def test_strip_junk_keeps_the_real_text_before_the_decay():
    assert transcribe.strip_junk(JUNK_AS) == "এটা ত্ৰিভুজৰ কালি হৈছে ভূমি আৰু উচ্চতাৰ পূৰ"
    assert transcribe.strip_junk(JUNK_UR).startswith("تکون کا رقبہ")


class FakeResp:
    def __init__(self, text):
        self.ok, self.status_code, self.text = True, 200, text

    def json(self):
        return {"text": self.text}


def _replies(monkeypatch, texts):
    it = iter(texts)
    calls = []
    monkeypatch.setattr(transcribe, "post_with_retry", lambda *a, **k: calls.append(1) or FakeResp(next(it)))
    transcribe.reset_stats()
    return calls


def test_junk_reply_is_retried_and_the_clean_one_cached(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"audio")
    calls = _replies(monkeypatch, [JUNK_AS, PROBE["as"]])
    r = transcribe.transcribe_file(audio, "as", "k", tmp_path / "cache")
    assert r["ok"] and r["text"] == PROBE["as"] and not r.get("junk") and r["calls"] == 2 and len(calls) == 2
    assert transcribe.STATS == {"junk_replies": 1, "junk_recovered": 1, "junk_unresolved": 0}
    again = transcribe.transcribe_file(audio, "as", "k", tmp_path / "cache")
    assert again["cached"] and again["text"] == PROBE["as"] and len(calls) == 2


def test_junk_on_every_try_is_marked_and_never_cached(tmp_path, monkeypatch):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"audio")
    calls = _replies(monkeypatch, [JUNK_UR] * 3)
    r = transcribe.transcribe_file(audio, "ur", "k", tmp_path / "cache")
    assert r["ok"] and r["junk"] and r["calls"] == 3 == len(calls)
    assert not transcribe.is_junk(r["text"]) and r["text"].startswith("تکون")
    assert not (tmp_path / "cache").exists() or not list((tmp_path / "cache").glob("*.json"))
    assert transcribe.STATS["junk_unresolved"] == 1
    json.dumps(r)


def test_qa_with_junk_back_transcription_is_unknown_not_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(qa, "transcribe_file", lambda *a, **k: {"ok": True, "junk": True, "text": "এটা", "cached": False})
    assert qa.qa_clip(tmp_path / "x.wav", PROBE["as"], "as", "k") is None
    monkeypatch.setattr(qa, "transcribe_file", lambda *a, **k: {"ok": True, "text": PROBE["as"], "cached": False})
    assert qa.qa_clip(tmp_path / "x.wav", PROBE["as"], "as", "k")["ok"]


def test_chunk_still_junk_after_retries_is_re_split(tmp_path, monkeypatch):
    sr = 16000
    p = tmp_path / "chunk_0001.wav"
    sf.write(p, np.zeros(20 * sr, "float32"), sr)

    def fake(path, lang, key, cache_dir=None):
        if "_p" not in path.name:   # the full chunk: junk after its retries, only the first words survive
            return {"ok": True, "junk": True, "text": "FIRST", "sec": 0.1, "cached": False, "calls": 3}
        return {"ok": True, "text": "FIRST HALF" if "_p0" in path.name else "SECOND HALF", "sec": 0.1,
                "cached": False, "calls": 1}

    monkeypatch.setattr(transcribe, "transcribe_file", fake)
    monkeypatch.setattr(transcribe, "get_key", lambda s: "k")
    c = Chunk(id=1, start=0.0, end=20.0, speech=19.0, path=str(p), regions=[[0.0, 9.0], [10.0, 20.0]])
    r = transcribe.transcribe_chunks([c], tmp_path / "w")[0]
    assert r["text"] == "FIRST HALF SECOND HALF" and r["asr_pieces"] == 2 and r["text_unsplit"] == "FIRST"
    assert abs(r["billed_sec"] - (20 * 3 + 20)) < 0.01   # 3 tries of the chunk + its two halves


def test_junk_piece_gives_no_alignment_text(tmp_path, monkeypatch):
    sr = 16000
    regions = [[0.2, 2.0], [2.6, 4.8], [5.3, 7.0]]
    p = tmp_path / "chunk_0001.wav"
    sf.write(str(p), np.zeros(int(7.5 * sr), "float32"), sr, subtype="PCM_16")
    ch = Chunk(id=1, start=10.0, end=17.5, speech=5.7, path=str(p), regions=[[a + 10, b + 10] for a, b in regions])
    text = "one two three. four five six seven. eight nine."
    punct = [{"id": 1, "start": 10.0, "end": 17.5, "ok": True, "text": text, "regions": ch.regions}]
    replies = {0: "one two three", 1: "four five", 2: "eight nine"}   # piece 1 decayed into junk after "five"

    def fake(path, lang, key, cache_dir):
        n = int(path.stem.split("_p")[1])
        return {"ok": True, "text": replies[n], "junk": n == 1, "cached": False, "sec": 0.1, "calls": 1}

    monkeypatch.setattr(align, "get_key", lambda name: "k")
    out, stats = align_chunks(punct, [ch], tmp_path, transcribe=fake)
    assert stats["failed_pieces"] == 1 and out[0]["anchors"] == []   # both pauses touch the junk piece
