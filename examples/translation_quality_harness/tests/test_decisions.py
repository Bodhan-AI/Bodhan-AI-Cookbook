"""Judge parsing and guardrails, fusion, calibration metrics and tuning, baseline diff, injector invariants."""

from __future__ import annotations

import random
import unicodedata

from bodhan_eval.calibration import evaluate, tune
from bodhan_eval.config import Thresholds
from bodhan_eval.fusion import fuse, mqm
from bodhan_eval.inject import text_ops
from bodhan_eval.inject.injector import diff_span, inject_text, perturb_source
from bodhan_eval.judge.judge import JudgeOutcome, group_candidates, majority, parse_confidence, parse_outcome, redact_indic
from bodhan_eval.languages import script_of
from bodhan_eval.models import Category, Flag, ItemResult, JudgeVerdict, Severity, Signal
from bodhan_eval.reporting import diff
from bodhan_eval.text.segment import tokens

SRC = "The meeting was moved to Monday because of rain."
TR = "बारिश के कारण बैठक सोमवार को कर दी गई।"


# judge ------------------------------------------------------------------------------


def test_redaction_removes_target_language_text_only():
    clean, removed = redact_indic("The word सोमवार means Monday; x² ≥ 3.")
    assert "सोमवार" not in clean and "Monday" in clean and "≥ 3" in clean and removed == len("सोमवार")


def test_parse_outcome_maps_token_indices_to_character_spans():
    raw = {
        "candidates": [{"id": "C1", "verdict": "Reject", "reason": "fine"}, {"id": "C9", "verdict": "confirm"}],
        "errors": [
            {
                "category": "omission",
                "severity": "major",
                "source_tokens": [2, 4],
                "translation_tokens": None,
                "confidence": 85,
                "explanation": "drops 'moved to'",
            },
            {"category": "mistranslation", "severity": "minor", "translation_tokens": [99, 120], "confidence": "low", "explanation": "x"},
            {"category": "speech_garbled", "severity": "major", "translation_tokens": [0, 0], "explanation": "not allowed"},
            {"category": "nonsense", "translation_tokens": [0, 0]},
        ],
    }
    out = parse_outcome(raw, tokens(SRC), tokens(TR), {"C1"}, cached=False)
    assert out.verdicts == {"C1": ("reject", "fine")}
    assert [e.category for e in out.errors] == [Category.OMISSION]
    s, e = out.errors[0].source_span
    assert SRC[s:e] == "was moved to" and out.errors[0].confidence == 0.85


def test_out_of_range_tokens_are_clamped_or_dropped():
    raw = {"errors": [{"category": "addition", "translation_tokens": [5, 500], "confidence": 0.8, "explanation": "x"}]}
    out = parse_outcome(raw, tokens(SRC), tokens(TR), set(), cached=False)
    assert out.errors[0].span[1] == len(TR)


def test_parse_confidence_variants():
    assert parse_confidence(90) == 0.9 and parse_confidence(0.4) == 0.4 and parse_confidence("75%") == 0.75 and parse_confidence("high") == 0.9


def test_majority_vote_keeps_errors_seen_by_most_samples():
    err = JudgeVerdict(category=Category.NUMBER, severity=Severity.MAJOR, confidence=0.9, span=(3, 5), explanation="x")
    other = JudgeVerdict(category=Category.ADDITION, severity=Severity.MINOR, confidence=0.9, span=(10, 12), explanation="y")
    merged = majority([JudgeOutcome(errors=[err]), JudgeOutcome(errors=[err, other]), JudgeOutcome(errors=[])])
    assert [e.category for e in merged.errors] == [Category.NUMBER]


# fusion -----------------------------------------------------------------------------


def sig(category=Category.NUMBER, score=0.55, span=(10, 12), hard=False, check="numbers"):
    return Signal(check=check, category=category, score=score, span=span, message="m", hard=hard)


def test_judge_confirmation_lifts_and_rejection_lowers():
    th = Thresholds()
    s = [sig()]
    cands = group_candidates(s)
    flags, _ = fuse(translation=TR, source=SRC, signals=s, candidates=cands, outcome=JudgeOutcome(verdicts={"C1": ("confirm", "")}), th=th)
    assert flags and flags[0].confidence > 0.8
    flags, rejected = fuse(translation=TR, source=SRC, signals=s, candidates=cands, outcome=JudgeOutcome(verdicts={"C1": ("reject", "")}), th=th)
    assert not flags and rejected[0].confidence < 0.3


def test_hard_signal_survives_a_judge_rejection():
    s = [sig(score=0.9, hard=True)]
    flags, _ = fuse(
        translation=TR, source=SRC, signals=s, candidates=group_candidates(s), outcome=JudgeOutcome(verdicts={"C1": ("reject", "")}), th=Thresholds()
    )
    assert flags


def test_independent_checks_combine_with_noisy_or():
    s = [sig(score=0.4, check="numbers"), sig(score=0.4, check="back_translation")]
    flags, _ = fuse(translation=TR, source=SRC, signals=s, candidates=None, outcome=None, th=Thresholds())
    assert flags[0].confidence == 0.64


def test_judge_only_minor_findings_are_discounted():
    minor = JudgeVerdict(category=Category.MISTRANSLATION, severity=Severity.MINOR, confidence=0.8, span=(0, 4), explanation="x")
    flags, rejected = fuse(translation=TR, source=SRC, signals=[], candidates=[], outcome=JudgeOutcome(errors=[minor]), th=Thresholds())
    assert not flags and rejected[0].confidence < 0.5


def test_mqm_penalty_and_score():
    f = Flag(category=Category.NUMBER, area="translation", severity=Severity.MAJOR, confidence=0.9, evidence=[], checks=[])
    assert mqm([f], "one two three four five six seven eight nine ten") == (5.0, 50.0)


# calibration ------------------------------------------------------------------------


def result(rid, labels, flags):
    return ItemResult(id=rid, lang="hi", status="scored", labels=labels, flags=flags)


def flag(cat, conf, span=None, source_span=None):
    return Flag(category=cat, area=cat.area, severity=Severity.MAJOR, confidence=conf, span=span, source_span=source_span, evidence=[], checks=["x"])


def test_metrics_cover_recall_false_alarms_localization_and_category():
    rs = [
        result("a", [{"category": "number", "type": "number", "span": [10, 12]}], [flag(Category.NUMBER, 0.9, span=(10, 12))]),
        result("b", [{"category": "omission", "type": "omission", "span": [0, 5], "field": "source"}], [flag(Category.MISTRANSLATION, 0.9, span=(30, 33))]),
        result("c", [], [flag(Category.ADDITION, 0.6, span=(1, 2))]),
        result("d", [], []),
    ]
    m = evaluate(rs, Thresholds(), {"a", "b", "c", "d"})
    h = m["headline"]
    assert h["detection_recall"] == 0.5  # an unrelated mistranslation does not detect b's omission
    assert h["false_alarm_rate"] == 0.5 and h["localization"] == 1.0 and h["category_accuracy"] == 1.0
    assert h["localization_recall"] == 0.5
    assert m["per_category"]["number"]["precision"] == 1.0 and m["per_category"]["addition"]["precision"] == 0.0


def test_source_field_labels_match_source_spans():
    rs = [
        result("a", [{"category": "omission", "type": "omission", "span": [20, 30], "field": "source"}], [flag(Category.OMISSION, 0.9, source_span=(22, 28))])
    ]
    assert evaluate(rs, Thresholds(), {"a"})["headline"]["localization"] == 1.0


def test_localization_reports_detected_and_all_error_denominators():
    labels = [{"category": "number", "span": [10, 12], "field": "translation"}]
    rs = [result("hit", labels, [flag(Category.NUMBER, 0.9, span=(10, 12))]), result("miss", labels, [])]
    m = evaluate(rs, Thresholds(), {"hit", "miss"})
    assert m["counts"]["with_span"] == 2
    assert m["counts"]["detected_with_span"] == 1
    assert m["headline"]["localization"] == m["per_language"]["hi"]["localization"] == 1.0
    assert m["headline"]["localization_recall"] == m["per_language"]["hi"]["localization_recall"] == 0.5


def test_cross_area_overlap_does_not_detect_or_localize():
    labels = [{"category": "number", "span": [10, 12], "field": "translation"}]
    rs = [result("a", labels, [flag(Category.SPEECH_GARBLED, 0.9, span=(10, 12))])]
    h = evaluate(rs, Thresholds(), {"a"})["headline"]
    assert h["detection_recall"] == h["localization_recall"] == 0.0
    assert h["localization"] is None


def test_spanless_known_category_requires_matching_category():
    rs = [result("a", [{"category": "number"}], [flag(Category.ADDITION, 0.9)])]
    assert evaluate(rs, Thresholds(), {"a"})["headline"]["detection_recall"] == 0.0


def test_overlap_can_detect_an_error_with_the_wrong_category():
    rs = [result("a", [{"category": "number", "span": [10, 12]}], [flag(Category.ADDITION, 0.9, span=(10, 12))])]
    h = evaluate(rs, Thresholds(), {"a"})["headline"]
    assert h["detection_recall"] == h["localization"] == 1.0
    assert h["category_accuracy"] == 0.0


def test_rejected_judge_candidate_at_another_span_is_not_a_missed_label():
    f = flag(Category.NUMBER, 0.2, span=(30, 33)).model_copy(update={"judge": "rejected"})
    r = result("a", [{"category": "number", "span": [10, 12]}], []).model_copy(update={"rejected": [f]})
    assert evaluate([r], Thresholds(), {"a"})["judge"]["rejected_but_labeled"] == 0


def test_tuning_lowers_false_alarms_without_hand_set_values():
    rs = []
    for i in range(6):
        rs.append(result(f"e{i}", [{"category": "addition", "type": "addition", "span": [0, 3]}], [flag(Category.ADDITION, 0.8, span=(0, 3))]))
        rs.append(result(f"c{i}", [], [flag(Category.ADDITION, 0.55, span=(0, 3))]))
    ids = {r.id for r in rs}
    assert evaluate(rs, Thresholds(), ids)["headline"]["false_alarm_rate"] == 1.0
    tuned, _ = tune(rs, Thresholds(), ids)
    m = evaluate(rs, tuned, ids)["headline"]
    assert m["false_alarm_rate"] == 0.0 and m["detection_recall"] == 1.0
    assert 0.55 < tuned.threshold(Category.ADDITION) <= 0.8


# diff -------------------------------------------------------------------------------


def test_diff_finds_regressions_and_improvements():
    base = [
        result("a", [], []).model_dump(mode="json"),
        {**result("b", [], [flag(Category.NUMBER, 0.9)]).model_dump(mode="json"), "mqm_penalty": 5.0},
    ]
    now = [result("a", [], [flag(Category.OMISSION, 0.9)]).model_copy(update={"mqm_penalty": 5.0}), result("b", [], [])]
    d = diff.compare(now, base)
    assert d["headline"]["regressed"] == 1 and d["headline"]["improved"] == 1
    assert d["regressions"][0]["new_categories"] == ["omission"]
    assert "Regressions" in diff.render(d, "base")


# injector ---------------------------------------------------------------------------


def _new_script_letters(old: str, new: str, extra: str = "") -> set[str]:
    allowed = set(old) | set(extra)
    return {ch for ch in new if ch not in allowed and unicodedata.category(ch).startswith("L") and script_of(ch) not in (None, "Latn")}


def test_injection_is_deterministic_balanced_and_labelled():
    rows = [
        {
            "id": f"i{k}",
            "source": f"In {1990 + k} the city of Pune had {k + 3} parks, and many trees.",
            "lang": "hi",
            "translation": f"{1990 + k} में पुणे शहर में {k + 3} पार्क थे, और बहुत सारे पेड़ थे।",
            "alternates": {"bn": "১৯৯০ সালে পুনে শহরে পার্ক ছিল।"},
        }
        for k in range(20)
    ]
    a = inject_text(rows, rate=0.5, seed=7)
    b = inject_text(rows, rate=0.5, seed=7)
    assert a == b
    labelled = [r for r in a if r["labels"]]
    assert len(labelled) == 10 and len({r["labels"][0]["type"] for r in labelled}) >= 4
    assert all("alternates" not in r for r in a)
    for r in labelled:
        lb = r["labels"][0]
        text = r["source"] if lb["field"] == "source" else r["translation"]
        assert 0 <= lb["span"][0] <= lb["span"][1] <= len(text) and lb["annotator"] == "injector"


def test_ops_never_invent_target_language_letters():
    pool = ["बाज़ार में आज बहुत भीड़ थी और दुकानें खुली थीं।", "बच्चे स्कूल गए।"]
    for seed in range(15):
        rng = random.Random(seed)
        for name in ("omission", "addition", "mistranslation", "number", "wrong_language"):
            out = text_ops.OPS[name](SRC + " It was cold.", TR + " ठंड थी।", rng, pool=pool, lang_script="Deva", alternates=None)
            if out is None:
                continue
            assert not _new_script_letters(TR + " ठंड थी।", out.translation, "".join(pool)), name


def test_number_op_keeps_native_digits_native():
    out = text_ops.number("It cost 25 rupees.", "इसकी कीमत २५ रुपये थी।", random.Random(1))
    changed = out.translation[out.label["span"][0] : out.label["span"][1]]
    assert changed != "२५" and all(unicodedata.decimal(c) is not None and not c.isascii() for c in changed)


def test_term_op_corrupts_inside_the_given_span():
    out = text_ops.term(SRC, TR, random.Random(2), term_span=(18, 24), lang_script="Deva")
    assert out.translation[:18] == TR[:18] and out.translation[24:] == TR[24:] and out.translation != TR


def test_format_op_breaks_a_table():
    md = "| # | पाठ |\n|---|---|\n| 1 | पहला |\n| 2 | दूसरा |"
    out = text_ops.format_error(md, md, random.Random(0))
    assert out is not None and out.translation != md and out.label["type"] == "format"


def test_english_perturbation_for_retranslation():
    rng = random.Random(0)
    new, info = perturb_source("Prices increase before the festival, and shops stay open late.", "mistranslation", rng, [])
    assert new != "Prices increase before the festival, and shops stay open late."
    assert diff_span("a b c d", "a b x d") == (4, 5)
