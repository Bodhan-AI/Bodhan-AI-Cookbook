from bodhan_eval.calibration import (
    build_calibration_cases,
    calculate_calibration,
    evaluate_injected_cases,
)
from bodhan_eval.injector import SUPPORTED_CATEGORIES, inject_labelled_error
from bodhan_eval.llm_judge import Finding, JudgeOutcome

HI = "अस्पताल 2 किलोमीटर दूर है, Android पर help@example.com लिखें।"


def test_every_category_can_be_injected():
    md = "Use **bold** and [docs](https://a.b)."
    for category in SUPPORTED_CATEGORIES:
        text = md if category == "formatting_mismatch" else HI
        result = inject_labelled_error(text, category, "hi", seed=3)
        assert result is not None, category
        assert result["mutated_text"] != text
        assert result["injected_category"] == category


def test_specific_injections():
    assert "मीटर" in inject_labelled_error("2 किलोमीटर दूर", "unit_mismatch", "hi")["mutated_text"]
    assert "examp1e" in inject_labelled_error(HI, "entity_mismatch", "hi")["mutated_text"]
    assert "शायद" in inject_labelled_error(HI, "addition", "hi")["mutated_text"]
    assert inject_labelled_error("[docs](https://a.b)", "formatting_mismatch", "hi")["mutated_text"] == "docs"
    assert inject_labelled_error("बैठक पहले होगी", "mistranslation", "hi")["mutated_text"] == "बैठक बाद होगी"


def test_mistranslation_never_replaces_units():
    result = inject_labelled_error("अस्पताल 2 किलोमीटर दूर है।", "mistranslation", "hi", seed=3)
    assert "किलोमीटर" in result["mutated_text"]


def test_injection_is_deterministic_and_none_when_impossible():
    assert inject_labelled_error(HI, "addition", "hi", 9) == inject_labelled_error(HI, "addition", "hi", 9)
    assert inject_labelled_error("कोई इकाई नहीं", "unit_mismatch", "hi") is None


def test_clean_false_alarm_rate():
    result = calculate_calibration([
        {"expected_categories": [], "predicted_categories": ["omission"]},
        {"expected_categories": [], "predicted_categories": []},
    ])
    assert result["overall"]["clean_cases"] == 2
    assert result["overall"]["clean_false_alarm_rate"] == 0.5


RECORDS = [
    {"id": f"hi-{i}", "language": "hi", "source_text": "The hospital is 2 kilometers away.",
     "translation": "अस्पताल 2 किलोमीटर दूर है।"} for i in range(3)
] + [
    {"id": f"bn-{i}", "language": "bn", "source_text": "The price is 500 rupees.",
     "translation": "দাম 500 টাকা।"} for i in range(3)
]


def test_builder_is_deterministic_and_mixes_languages():
    first = build_calibration_cases(RECORDS, ["number_mismatch", "omission"], per_category=4, clean_cases=2)
    second = build_calibration_cases(RECORDS, ["number_mismatch", "omission"], per_category=4, clean_cases=2)
    assert first == second
    numbers = [c for c in first if c["injected_category"] == "number_mismatch"]
    assert {c["language"] for c in numbers} == {"hi", "bn"}
    assert sum(1 for c in first if c["injected_category"] == "") == 2
    assert all("source_text" in c for c in first)


def test_modes_reference_free_and_llm():
    cases = build_calibration_cases(RECORDS, ["number_mismatch", "omission"], per_category=2, clean_cases=2)

    class FakeJudge:
        def judge(self, source, translation, language):
            findings = []
            if "999" in translation:
                findings.append(Finding(evidence_source="translation", category="number_unit",
                                        severity="high", span="999", explanation="x", confidence=0.9))
            return JudgeOutcome(status="ok", findings=findings)

    result = evaluate_injected_cases(cases, judge=FakeJudge())

    assert result["modes"]["reference_based"]["overall"]["recall"] == 1.0
    free = result["modes"]["reference_free"]["categories"]
    assert free["number_mismatch"]["recall"] == 1.0
    assert free["omission"]["recall"] == 0.0   # needs a reference or the judge
    judge = result["modes"]["llm_judge"]["categories"]
    assert judge["number_unit"]["tp"] == 2
    assert judge["omission"]["fn"] == 2
    assert result["llm_judge_status"] == {"ok": len(cases)}


def test_old_style_cases_still_work_without_source():
    result = evaluate_injected_cases([{
        "id": "x", "clean_translation": "कीमत 500 रुपये है",
        "mutated_text": "कीमत 999 रुपये है", "injected_category": "number_mismatch",
    }])
    assert result["metrics"]["overall"]["recall"] == 1.0
    assert result["modes"]["reference_free"] is None
