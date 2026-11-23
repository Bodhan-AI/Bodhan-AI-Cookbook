"""The LLM judge against the stub: answer parsing (TESTING.md §2), adaptive replicas, jury, repeat-consistency
and long-text windows (K2). The stub answers by rules on the prompt, so each case is exact and costs nothing.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

import pytest

from bodhan_eval.calibrate import judge_consistency
from bodhan_eval.checks.judge import JudgeCheck, judge_windows, sampled
from bodhan_eval.models import Item, ItemResult
from bodhan_eval.normalize import ws_tokens
from bodhan_eval.pipeline import run_check

SRC = "Water boils at 100 degrees."
TGT = "पानी 100 डिग्री पर उबलता है।"
# translation tokens: [0]पानी [1]100 [2]डिग्री [3]पर [4]उबलता [5]है।
# source tokens:      [0]Water [1]boils [2]at [3]100 [4]degrees.
INDIC = re.compile(r"[ऀ-෿]")
# The three prompt orders start with different blocks, so a rule can answer one replica and not another.
ORDER_PREFIX = ("SOURCE (English)", "TRANSLATION (", "AUTOMATIC FINDINGS")


def err(
    category: object = "number",
    target: object = (1, 1),
    source: object = (3, 3),
    severity: str = "critical",
    explanation: str = "the number changed",
    confidence: object = 0.9,
) -> dict:
    return {
        "category": category,
        "severity": severity,
        "target_tokens": list(target) if isinstance(target, tuple) else target,
        "source_tokens": list(source) if isinstance(source, tuple) else source,
        "explanation": explanation,
        "confidence": confidence,
    }


def answer(*errors: dict) -> str:
    return json.dumps({"errors": list(errors)}, ensure_ascii=False)


NONE = answer()
NUMBER = answer(err())


@pytest.fixture
def judge(start_stub, make_ctx) -> Callable[..., tuple[ItemResult, list[dict]]]:
    """judge(rules, source=..., translation=..., **judge_cfg) -> (item result, stub requests)."""

    def run(rules: list[dict], source: str = SRC, translation: str = TGT, **cfg: object):
        stub = start_stub({"chat": rules})
        ctx = make_ctx(stub, judge=True)
        ctx.judge_cfg.update({"repeat_sample": 0.0, **cfg})
        r = ItemResult(item=Item(id="t", lang="hi", source=source, translation=translation))
        r.checks["judge"] = run_check(JudgeCheck(), r, ctx)
        return r, stub.requests

    return run


def found(r: ItemResult) -> set[tuple[str, str | None, str | None]]:
    out = set()
    for s in r.signals:
        text = r.item.translation[s.span[0] : s.span[1]] if s.span else None  # type: ignore[index]
        stext = r.item.source[s.source_span[0] : s.source_span[1]] if s.source_span else None
        out.add((s.category, text, stext))
    return out


# ---------------------------------------------------------------------------
# parsing: must catch / must not flag (TESTING.md §2, judge row)
# ---------------------------------------------------------------------------

PARSE_CASES = [
    # id, judge answer, expected (category, translation text, source text), invalid outputs, dropped errors, calls
    ("valid number error has exact spans", NUMBER, {("number", "100", "100")}, 0, 0, 2),
    ("omission located on the source only", answer(err("omission", None, (0, 1))), {("omission", None, "Water boils")}, 0, 0, 2),
    ("wrong language needs no location", answer(err("wrong_language", None, None)), {("wrong_language", None, None)}, 0, 0, 2),
    ("end index past the last token is clamped", answer(err("mistranslation", (4, 99), None)), {("mistranslation", "उबलता है।", None)}, 0, 0, 2),
    ("category alias is canonicalised", answer(err("Numbers")), {("number", "100", "100")}, 0, 0, 2),
    ("bad JSON", "The translation looks fine to me.", set(), 3, 0, 3),
    ("JSON without an errors list", '{"verdict": "fine"}', set(), 3, 0, 3),
    ("start index out of range", answer(err(target=(7, 9), source=None)), set(), 0, 2, 2),
    ("reversed indices", answer(err(target=(3, 1), source=None)), set(), 0, 2, 2),
    ("negative index", answer(err(target=(-1, 2), source=None)), set(), 0, 2, 2),
    ("non-integer indices", answer(err(target=["1", "2"], source=None)), set(), 0, 2, 2),
    ("unknown category", answer(err("style")), set(), 0, 2, 2),
    ("no location for a mistranslation", answer(err("mistranslation", None, None)), set(), 0, 2, 2),
    ("an error entry that is not an object", '{"errors": ["number"]}', set(), 0, 2, 2),
]  # fmt: skip


@pytest.mark.parametrize(
    ("content", "expected", "invalid", "dropped", "calls"),
    [c[1:] for c in PARSE_CASES],
    ids=[c[0] for c in PARSE_CASES],
)
def test_judge_answer_parsing(judge, content: str, expected: set, invalid: int, dropped: int, calls: int) -> None:
    r, requests = judge([{"content": content}])
    assert r.checks["judge"]["status"] == "ok"
    assert found(r) == expected
    assert r.metrics["judge_invalid_outputs"] == invalid
    assert r.metrics["judge_dropped_errors"] == dropped
    assert len(requests) == calls  # agreeing replicas stop at two; invalid ones are replaced up to the cap of three


def test_indic_text_in_the_explanation_is_masked(judge) -> None:
    r, _ = judge([{"content": answer(err(explanation="the word पानी should be जल here"))}])
    assert r.signals and all(not INDIC.search(s.evidence) for s in r.signals)
    assert "[…]" in r.signals[0].evidence


@pytest.mark.parametrize(("given", "kept"), [(0.9, 0.9), (7, 1.0), (-2, 0.0), ("high", 0.7), (None, 0.7)])
def test_confidence_is_clamped_or_defaulted(judge, given: object, kept: float) -> None:
    r, _ = judge([{"content": answer(err(confidence=given))}])
    assert r.signals[0].details["confidence"] == kept


def test_judge_asks_for_the_strict_verdict_schema(judge) -> None:
    _, requests = judge([{"content": NUMBER}])
    schema = requests[0]["json_schema"]["schema"]
    assert schema["required"] == ["errors"] and schema["additionalProperties"] is False
    item = schema["properties"]["errors"]["items"]
    assert set(item["properties"]["category"]["enum"]) == {
        "omission", "addition", "mistranslation", "number", "term", "format", "wrong_language",
    }  # fmt: skip


def test_two_agreeing_replicas_flag(judge, fuse_item) -> None:
    r, _ = judge([{"content": NUMBER}])
    flags, _ = fuse_item(r)
    assert [f.category for f in flags] == ["number"]
    assert r.metrics["judge_agree_categories"] is True and r.metrics["judge_agree_binary"] is True


# ---------------------------------------------------------------------------
# K2a: adaptive replicas
# ---------------------------------------------------------------------------


def test_agreeing_replicas_stop_at_two(judge) -> None:
    r, requests = judge([{"content": NUMBER}])
    assert len(requests) == 2 and r.metrics["judge_replicas"] == 2
    assert requests[0]["body_hash"] != requests[1]["body_hash"]  # re-ordered, not repeated


def test_replicas_do_not_see_each_others_verdicts(judge) -> None:
    """Replica 2's AUTOMATIC FINDINGS hold the earlier checks' findings only, never replica 1's answer."""
    _, requests = judge([{"content": NUMBER}])
    assert "Judge (" not in requests[1]["user"]
    assert "AUTOMATIC FINDINGS (may be false alarms):\n(none)" in requests[1]["user"]


def test_disagreement_runs_a_third_replica_and_the_majority_flags(judge, fuse_item) -> None:
    r, requests = judge([{"starts_with": ORDER_PREFIX[1], "content": NONE}, {"content": NUMBER}])
    assert len(requests) == 3 and r.metrics["judge_replicas"] == 3 and r.metrics["judge_tie_break"] is True
    assert r.state["judge_majority"] == ["number"]
    assert r.metrics["judge_agree_categories"] is False  # order consistency is about the first two
    flags, _ = fuse_item(r)
    assert [f.category for f in flags] == ["number"]  # two of three replicas: noisy-OR passes the threshold


def test_a_minority_finding_goes_to_review_not_to_flags(judge, fuse_item) -> None:
    r, _ = judge([{"starts_with": ORDER_PREFIX[0], "content": NUMBER}, {"content": NONE}])
    assert r.state["judge_majority"] == []
    flags, review = fuse_item(r)
    assert flags == [] and [f.category for f in review] == ["number"]


def test_max_replicas_caps_the_tie_break(judge) -> None:
    r, requests = judge([{"starts_with": ORDER_PREFIX[1], "content": NONE}, {"content": NUMBER}], max_replicas=2)
    assert len(requests) == 2 and r.metrics["judge_replicas"] == 2


def test_an_invalid_replica_is_replaced(judge) -> None:
    r, requests = judge([{"starts_with": ORDER_PREFIX[0], "content": "no idea"}, {"content": NUMBER}])
    assert len(requests) == 3
    assert r.metrics["judge_invalid_outputs"] == 1
    assert r.metrics["judge_agree_categories"] is True  # the two valid replicas agree


def test_single_replica_when_asked(judge) -> None:
    r, requests = judge([{"content": NUMBER}], replicas=1)
    assert len(requests) == 1 and "judge_agree_categories" not in r.metrics


# ---------------------------------------------------------------------------
# K2b: jury
# ---------------------------------------------------------------------------


def test_jury_asks_each_model_once_and_majority_decides(judge, fuse_item) -> None:
    rules = [{"model": "juror-c", "content": NONE}, {"content": NUMBER}]
    r, requests = judge(rules, models=["juror-a", "juror-b", "juror-c"])
    assert [q["model"] for q in requests] == ["juror-a", "juror-b", "juror-c"]  # three votes: no tie, no 4th call
    assert sorted({s.details["model"] for s in r.signals}) == ["juror-a", "juror-b"]
    assert "juror-a" in r.signals[0].evidence or "juror-b" in r.signals[0].evidence
    flags, _ = fuse_item(r)
    assert [f.category for f in flags] == ["number"]


def test_without_a_jury_the_configured_model_judges(judge) -> None:
    _, requests = judge([{"content": NUMBER}], models=[])
    assert {q["model"] for q in requests} == {"stub-judge"}


# ---------------------------------------------------------------------------
# K2c: repeat-consistency
# ---------------------------------------------------------------------------


def test_repeat_sample_resends_the_identical_prompt_and_adds_no_evidence(judge) -> None:
    r, requests = judge([{"content": NUMBER}], repeat_sample=1.0)
    assert len(requests) == 3
    assert requests[2]["body_hash"] == requests[0]["body_hash"]  # the same request; only the cache key differs
    assert r.metrics["judge_repeat_agree"] is True
    assert len(r.signals) == 2  # one per replica; the repeat is a measurement, not evidence


def test_repeat_disagreement_is_reported(judge) -> None:
    r, _ = judge([{"content": NUMBER, "times": 2}, {"content": NONE}], repeat_sample=1.0)
    assert r.metrics["judge_repeat_agree"] is False


def test_repeat_sample_zero_sends_nothing_extra(judge) -> None:
    r, requests = judge([{"content": NUMBER}], repeat_sample=0.0)
    assert len(requests) == 2 and "judge_repeat_agree" not in r.metrics


def test_repeat_sampling_is_deterministic() -> None:
    keys = [f"hi/item-{k}" for k in range(2000)]
    first = [sampled(k, 0.1) for k in keys]
    assert first == [sampled(k, 0.1) for k in keys]
    assert 0.08 < sum(first) / len(keys) < 0.12
    assert not any(sampled(k, 0.0) for k in keys) and all(sampled(k, 1.0) for k in keys)


def test_consistency_report_keeps_order_and_repeat_apart() -> None:
    def res(metrics: dict) -> ItemResult:
        r = ItemResult(item=Item(id="x", lang="hi", source="s", translation="t"))
        r.metrics.update(metrics)
        return r

    report = judge_consistency(
        [
            res({"judge_agree_binary": True, "judge_agree_categories": True, "judge_replicas": 2}),
            res({"judge_agree_binary": True, "judge_agree_categories": False, "judge_replicas": 3, "judge_tie_break": True}),
            res({"judge_agree_binary": True, "judge_agree_categories": True, "judge_replicas": 2, "judge_repeat_agree": False}),
        ]
    )  # fmt: skip
    assert report["same_categories_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert report["tie_break_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert report["repeat"] == {"items": 1, "same_categories_rate": 0.0}


# ---------------------------------------------------------------------------
# K2d: long translations are judged in overlapping windows
# ---------------------------------------------------------------------------

LONG_SRC = " ".join(f"Sentence {i} has one two three four five six seven." for i in range(30))
LONG_TGT = " ".join(f"शब्द{i} एक दो तीन चार पांच छह सात आठ नौ।" for i in range(30))  # 10 tokens per sentence


def test_short_text_is_one_window() -> None:
    assert judge_windows(SRC, TGT, max_tokens=1200) == [(0, 6, 0, 5)]


def test_long_text_windows_overlap_by_one_segment_and_cover_everything() -> None:
    wins = judge_windows(LONG_SRC, LONG_TGT, max_tokens=100)
    assert [(t0, t1) for t0, t1, _, _ in wins] == [(0, 100), (90, 190), (180, 280), (270, 300)]
    n_src = len(ws_tokens(LONG_SRC))
    assert wins[0][2] == 0 and wins[-1][3] == n_src
    for (_, t1, s0, s1), (u0, _, v0, _) in zip(wins, wins[1:]):
        assert u0 == t1 - 10  # the last sentence of a window opens the next one
        assert s0 < v0 < s1  # source windows overlap too
    for t0, t1, s0, s1 in wins:  # each source window covers the proportional range of its translation window
        assert s0 <= t0 * n_src // 300 and s1 >= min(n_src, -(-t1 * n_src // 300))


def test_one_oversized_segment_is_cut_into_window_sized_pieces() -> None:
    tgt = " ".join(["शब्द"] * 250)  # no sentence end at all
    assert [(t0, t1) for t0, t1, _, _ in judge_windows("word " * 200, tgt, max_tokens=100)] == [
        (0, 100),
        (100, 200),
        (200, 250),
    ]


def test_window_token_indices_map_back_to_the_full_text(judge) -> None:
    rules = [
        # only the second window starts with sentence 9; its local token 0 is that sentence's first word
        {"contains": "\n[0]शब्द9 ", "content": answer(err("mistranslation", (0, 0), None))},
        {"content": NONE},
    ]
    r, requests = judge(rules, source=LONG_SRC, translation=LONG_TGT, window_tokens=100)
    assert r.checks["judge"]["status"] == "ok"
    assert r.metrics["judge_windows"] == 4
    assert len(requests) == 8  # 4 windows x 2 replicas
    assert found(r) == {("mistranslation", "शब्द9", None)}
    assert "part 2 of 4" in requests[1]["user"]  # replica 1 walks the windows in order


def test_too_many_windows_skip_the_judge(judge) -> None:
    r, requests = judge([{"content": NONE}], source=LONG_SRC, translation=LONG_TGT, window_tokens=100, max_windows=3)
    assert r.checks["judge"]["status"] == "skipped" and "windows" in r.checks["judge"]["reason"]
    assert requests == []
