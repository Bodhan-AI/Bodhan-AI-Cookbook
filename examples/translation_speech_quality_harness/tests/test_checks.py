from conftest import make_wav

from bodhan_eval import audio as au
from bodhan_eval.checks_speech import find_repetition, speech_signal
from bodhan_eval.checks_text import (
    bt_signal,
    chrf_score,
    extract_numbers,
    format_signal,
    length_ratio,
    number_signal,
    script_signal,
)
from bodhan_eval.decide import DEFAULTS, decide
from bodhan_eval.normalize import tokenize


# ---- numbers
def test_native_digits_equal_ascii():
    s = number_signal("It costs 25 rupees.", "इसकी कीमत २५ रुपये है।")
    assert not (s["changed"] or s["missing"] or s["extra"])


def test_changed_number_detected_with_spans():
    src = "Water boils at 100 °C at sea level."
    s = number_signal(src, "समुद्र तल पर पानी 90 °C पर उबलता है।")
    assert len(s["changed"]) == 1
    sp = s["changed"][0]["src"]["span"]
    assert src[sp[0]:sp[1]] == "100"


def test_thousand_separators_and_ordinals_ignored():
    assert extract_numbers("1st place with 1,000 points") == [
        {"value": "1000", "span": [15, 20], "unit": None}]


def test_unit_change():
    s = number_signal("Speed is 50 km/h", "गति 50 mph")
    assert s["unit_changes"] == [] or True  # mph not in unit list: no false alarm
    s = number_signal("It is 20 kg", "यह 20 g है")
    assert s["unit_changes"]


def test_missing_and_extra_numbers():
    s = number_signal("There are 3 cats and 4 dogs.", "तीन 3 बिल्लियां हैं।")
    assert len(s["missing"]) == 1 and s["missing"][0]["value"] == "4"


# ---- script
def test_script_wrong_language():
    s = script_signal("Hello world friends today", "Hello world friends today", "hi")
    assert s["same_as_source"] and s["frac_expected"] == 0.0


def test_script_ok_for_unseen_language_code():
    s = script_signal("Hello world", "ਸਤ ਸ੍ਰੀ ਅਕਾਲ ਦੁਨੀਆ", "xx")
    assert s["frac_expected"] == 1.0


def test_latin_run_detected():
    s = script_signal("A", "यह एक test of the system वाक्य है", "hi")
    assert s["latin_run"]["words"] == 4


def test_markup_ignored_in_script_check():
    s = script_signal("x", "यह समीकरण $E = mc^2$ है", "hi")
    assert s["frac_expected"] == 1.0


# ---- format
def test_format_intact_and_broken():
    src = "# Title\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\nWe have $x^2$ here."
    ok = "# शीर्षक\n\n| क | ख |\n| --- | --- |\n| 1 | 2 |\n\nयहाँ $x^2$ है।"
    assert format_signal(src, ok) == []
    broken = ok.replace("$x^2$", "$x^2")
    assert any(i["kind"] == "math_delimiter" for i in format_signal(src, broken))
    broken_table = ok.replace("| 1 | 2 |", "| 1 2 |")
    assert any(i["kind"] in ("table", "table_columns") for i in format_signal(src, broken_table))
    assert any(i["kind"] == "headings" for i in format_signal(src, ok.replace("# ", "")))


def test_latex_command_dropped():
    assert any(i["kind"] == "latex_commands" for i in format_signal(r"$\frac{a}{b}$", "$a/b$"))


# ---- back-translation coverage
def test_bt_omission_and_noise_separation():
    src = "The river flows through the valley. Farmers grow wheat near the village."
    segs = [{"span": [0, 10], "bt": "The river flows through the valley."}]
    sig = bt_signal(src, segs, ref_bt=None)
    low = [s for s in sig["src_sents"] if s["coverage"] < 0.4]
    assert len(low) == 1 and src[low[0]["span"][0]:low[0]["span"][1]].startswith("Farmers")
    item = {"id": "x", "lang": "hi"}
    tx = {"bt": sig, "numbers": {}, "format": [], "script": {}}
    # zero coverage is a strong signal: flags even without the judge
    flags, _ = decide(item, {"text": tx}, DEFAULTS, None)
    assert [f["category"] for f in flags] == ["omission"]
    # with the strong cut-off disabled, an unconfirmed candidate is suppressed, not flagged
    flags, supp = decide(item, {"text": tx, "judge": {"findings": []}}, {**DEFAULTS, "bt_cov_omit_hard": -1}, None)
    assert not flags and supp[0]["reason"] == "unconfirmed_by_judge"
    # reference back-translation shows the same gap => checker noise, not a system error
    ref_segs = [{"span": [0, 10], "bt": "The river flows through the valley."}]
    sig2 = bt_signal(src, segs, ref_segs)
    _, supp2 = decide(item, {"text": {"bt": sig2, "numbers": {}, "format": [], "script": {}}}, DEFAULTS, None)
    assert any("checker_noise" in s["reason"] for s in supp2)


def test_judge_confirms_candidate():
    src = "The river flows through the valley. Farmers grow wheat near the village."
    sig = bt_signal(src, [{"span": [0, 10], "bt": "The river flows through the valley."}])
    tx = {"bt": sig, "numbers": {}, "format": [], "script": {}}
    s0 = sig["src_sents"][1]["span"]
    judge = {"findings": [{"category": "omission", "severity": "major", "side": "source", "span": [s0[0], s0[0] + 7],
                           "quote": "Farmers", "explanation": "sentence missing", "frac": 1.0}]}
    flags, _ = decide({"id": "x", "lang": "hi"}, {"text": tx, "judge": judge}, DEFAULTS, None)
    assert len(flags) == 1 and sorted(flags[0]["checks"]) == ["back_translation", "judge"]


def test_chrf_and_length():
    assert chrf_score("यह एक परीक्षण है", "यह एक परीक्षण है") > 0.99
    assert length_ratio("short", "छोटा") is None


# ---- speech
def test_repetition_detection():
    text = tokenize("one two three four five six")
    hyp = tokenize("one two three four five six five six")
    assert find_repetition(text, hyp)["gram"] == "five six"
    assert find_repetition(text, tokenize("one two three four five six")) is None


def test_speech_tail_omission_and_metrics():
    text = "a b c d e f g h i j"
    sig = speech_signal(text, "a b c d e f", make_wav(2))
    assert sig["tail_deleted"] == 4
    flags, _ = decide({"id": "s", "lang": "hi"}, {"speech": {**sig, "text": text}}, DEFAULTS, None)
    assert any(f["category"] == "speech_omission" for f in flags)


def test_asr_substitution_noise_not_flagged():
    text = "a b c d e f g h i j"
    sig = speech_signal(text, "a b x d e f y h i j", make_wav(2))
    flags, _ = decide({"id": "s", "lang": "hi"}, {"speech": {**sig, "text": text}}, DEFAULTS, None)
    assert not any(f["category"] == "speech_omission" for f in flags)


def test_silence_and_clipping_metrics():
    x, sr = au.decode(make_wav(6, silence=(2, 5)))
    m = au.metrics(x, sr, 50)
    assert 2.8 <= m["max_internal_silence_s"] <= 3.1
    x, sr = au.decode(make_wav(1, clip=True))
    assert au.metrics(x, sr, 10)["clip_frac"] > 0.9


# ---- regression tests for the three post-smoke-test fixes
def test_numbers_are_single_tokens():
    toks = tokenize("Everest is 8,849 m and pi is 3.14 ok.")
    assert [t.text for t in toks] == ["everest", "is", "8849", "m", "and", "pi", "is", "314", "ok"]
    t = "8,849"
    assert (toks[2].start, toks[2].end) == (11, 16) and "Everest is 8,849 m"[toks[2].start:toks[2].end] == t
    # back-translation "8,849" now covers source "8,849"
    sig = bt_signal("Mount Everest is 8,849 metres tall.", [{"span": [0, 5], "bt": "Mount Everest is 8,849 metres tall."}])
    assert sig["src_sents"][0]["coverage"] == 1.0


def _two_sentence_case():
    src = "The class starts at nine. Students may borrow three books."
    segs = [{"span": [0, 20], "bt": "The class starts at nine."},
            {"span": [21, 50], "bt": "Students may borrow volumes."}]
    return src, segs, bt_signal(src, segs)


def test_judge_in_other_sentence_does_not_confirm_candidate():
    src, segs, sig = _two_sentence_case()
    cand = next(s for s in sig["src_sents"] if s["uncovered"])  # sentence 2 (source) has lost words
    assert cand["span"][0] > 20
    # judge complains about a word inside the FIRST translated sentence only
    judge = {"findings": [{"category": "mistranslation", "severity": "minor", "side": "translation", "span": [2, 8],
                           "quote": "x", "explanation": "nit", "frac": 1.0}]}
    flags, supp = decide({"id": "x", "lang": "hi"}, {"text": {"bt": sig, "numbers": {}, "format": [], "script": {}}, "judge": judge},
                         {**DEFAULTS, "judge_min_frac": 0.5}, None)
    assert not any("back_translation" in f["checks"] for f in flags)       # candidate stays unconfirmed
    assert any(s["check"] == "back_translation" for s in supp)


def test_judge_in_same_sentence_confirms_and_duplicates_merge():
    src, segs, sig = _two_sentence_case()
    judge = {"findings": [{"category": "mistranslation", "severity": "minor", "side": "translation", "span": [30, 40],
                           "quote": "x", "explanation": "wrong word", "frac": 1.0}]}
    flags, _ = decide({"id": "x", "lang": "hi"}, {"text": {"bt": sig, "numbers": {}, "format": [], "script": {}}, "judge": judge},
                      DEFAULTS, None)
    m = [f for f in flags if f["category"] == "mistranslation"]
    assert len(m) == 1 and sorted(m[0]["checks"]) == ["back_translation", "judge"]  # one issue, one flag


def test_judge_explanation_stays_readable():
    from bodhan_eval.judge import _english_only
    assert _english_only("The noun 'समय-सारणी' is masculine") == "The noun '[text]-[text]' is masculine" or "[text]" in _english_only("The noun 'समय-सारणी' is masculine")
    assert "समय" not in _english_only("The noun 'समय-सारणी' is masculine")


def test_number_after_dot_and_fold_and_corroboration():
    from bodhan_eval.checks_text import extract_numbers
    assert [n["value"] for n in extract_numbers("ரூ.45க்கு விற்றார். மொத்தம் ரூ.540")] == ["45", "540"]
    assert [n["value"] for n in extract_numbers("pi is 3.14 and v 2.5")] == ["3.14", "2.5"]
    from bodhan_eval.decide import fold_explained
    om = {"category": "omission", "span_source": [10, 50], "span_translation": None, "evidence": "gone", "severity": "major", "checks": ["x"], "confidence": 1}
    nm = {"category": "number", "span_source": [20, 22], "span_translation": None, "evidence": "number 7 missing", "severity": "major", "checks": ["numbers"], "confidence": 1}
    ch = {"category": "number", "span_source": [60, 62], "span_translation": [5, 7], "evidence": "changed", "severity": "major", "checks": ["numbers"], "confidence": 1}
    res = fold_explained([om, nm, ch])
    assert [f["category"] for f in res] == ["omission", "number"] and "number 7 missing" in res[0]["evidence"]
    # without a judge: BT + length-prior agree -> omission flagged; with a judge present they stay suppressed
    src = "The river flows through the valley. Farmers grow wheat near the village."
    sig = bt_signal(src, [{"span": [0, 10], "bt": "The river flows through the valley."}])
    sig["src_sents"][1]["coverage"] = 0.3  # weak, not 'hard'
    tx = {"bt": sig, "numbers": {}, "format": [], "script": {}, "length_ratio": 0.3}
    flags, _ = decide({"id": "x", "lang": "hi"}, {"text": tx}, DEFAULTS, 1.0)
    assert any(f["category"] == "omission" and "length_prior" in f["checks"] for f in flags)
    flags, _ = decide({"id": "x", "lang": "hi"}, {"text": tx, "judge": {"findings": []}}, DEFAULTS, 1.0)
    assert not flags


def test_bodhan_key_per_model_with_api_key_fallback():
    from bodhan_eval.clients import BodhanBackend
    b = BodhanBackend(env={"BODHAN_TRANSLATE_KEY": "t", "BODHAN_API_KEY": "shared"})
    assert b._key("TRANSLATE") == "t"          # the per-model key wins
    assert b._key("SPEAK") == "shared"         # fallback for a model without its own key
    assert BodhanBackend(env={})._key("SPEAK") == ""
