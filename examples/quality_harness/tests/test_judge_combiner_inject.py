import json

from bodhan_eval.combiner import combine
from bodhan_eval.config import load_thresholds
from bodhan_eval.inject import build_injected
from bodhan_eval.judge import OpenAIJudge, judge_to_evidence, parse_judge_output
from bodhan_eval.models import Evidence, Item

THR = load_thresholds()


def test_judge_usage_distinguishes_cached_calls():
    judge = OpenAIJudge("https://example.test", "test", "model", None, None, None)
    judge._record_usage({"prompt_tokens": 100, "completion_tokens": 20}, cached=False)
    judge._record_usage({"prompt_tokens": 100, "completion_tokens": 20}, cached=True)
    assert judge.usage["calls"] == 2
    assert judge.usage["prompt_tokens"] == 200
    assert judge.usage["uncached_calls"] == 1
    assert judge.usage["uncached_completion_tokens"] == 20


# ---- judge --------------------------------------------------------------------------

def test_judge_parse_and_verbatim_location():
    raw = json.dumps({
        "alignment": [{"source_clause": "Water boils at 100", "translation_clause": "पानी 90"}],
        "errors": [{"category": "number_unit", "severity": "major",
                    "translation_quote": "90", "source_quote": "100",
                    "explanation": "number changed", "confidence": 0.9}],
        "verdict": "error",
    })
    res = parse_judge_output(raw)
    assert res.verdict == "error" and len(res.errors) == 1
    it = Item(id="x", source="Water boils at 100", lang="hi", translation="पानी 90 है")
    evs = judge_to_evidence(res, it)
    e = evs[0]
    assert e.target_span is not None and it.translation[e.target_span[0]:e.target_span[1]] == "90"
    assert e.details["located"] is True


def test_judge_nonverbatim_quote_demoted():
    raw = json.dumps({"errors": [{"category": "omission", "severity": "major",
                                  "translation_quote": "not present text",
                                  "source_quote": "", "explanation": "x", "confidence": 0.8}],
                      "verdict": "error"})
    it = Item(id="x", source="abc", lang="hi", translation="xyz")
    evs = judge_to_evidence(parse_judge_output(raw), it)
    assert evs[0].details["located"] is False
    assert evs[0].target_span is None


def test_judge_json_repair_trailing_comma():
    raw = '{"errors": [], "verdict": "ok",}'
    assert parse_judge_output(raw).parse_ok is True


# ---- combiner -----------------------------------------------------------------------

def test_strong_deterministic_flags_alone():
    ev = [Evidence(check="t2", category="number_unit", strength="strong", noisy=False,
                   source_span=(0, 3))]
    out = combine(ev, THR, "hi")
    assert len(out.flags) == 1 and out.flags[0].category == "number_unit"


def test_lone_noisy_is_uncertain_not_flagged():
    ev = [Evidence(check="roundtrip", category="mistranslation", strength="medium", noisy=True,
                   score=0.5)]
    out = combine(ev, THR, "hi")
    assert not out.flags
    assert "mistranslation" in out.uncertain_categories


def test_two_independent_noisy_corroborate():
    ev = [Evidence(check="roundtrip", category="omission", strength="medium", noisy=True),
          Evidence(check="t7_sentence_coverage", category="omission", strength="weak", noisy=True)]
    out = combine(ev, THR, "hi")
    assert any(f.category == "omission" for f in out.flags)


def test_judge_with_verified_quote_can_flag_semantic():
    ev = [Evidence(check="judge", category="omission", strength="medium", noisy=True,
                   score=0.9, target_span=(0, 3), details={"located": True})]
    out = combine(ev, THR, "hi")
    assert any(f.category == "omission" for f in out.flags)


# ---- injector reproducibility -------------------------------------------------------

def test_injector_deterministic():
    items = [{"id": f"i{i}", "source": f"Sentence {i} has 5 items. More here.",
              "lang": "hi", "translation": "वाक्य ५ में 5 वस्तुएं हैं। और यहाँ।"} for i in range(6)]
    a = build_injected(items, seed=3, clean_fraction=0.5)
    b = build_injected(items, seed=3, clean_fraction=0.5)
    assert a == b
    labelled = [x for x in a if x.get("label_source") == "injector"]
    assert labelled and all(x["labels"][0]["label_source"] == "injector" for x in labelled)
