"""Regression tests for the draft issues fixed in Phase 2 (K3, K6, K8; see docs/dev/BUILD_PLAN.md)."""

from __future__ import annotations

from collections.abc import Callable

from bodhan_eval.checks.roundtrip import BackTranslationCheck, EntityCheck
from bodhan_eval.fusion import fuse
from bodhan_eval.models import Item, ItemResult, Signal
from bodhan_eval.pipeline import prefetch_transliterations, run_check

# ---------------------------------------------------------------------------
# K3: check instances hold no per-item state
# ---------------------------------------------------------------------------


def test_k3_back_translation_keeps_no_state_on_the_shared_instance(start_stub, make_ctx) -> None:
    stub = start_stub({"translate": {"en": {"पानी उबलता है।": "Water boils.", "बर्फ पिघलती है।": "Ice melts."}}})
    ctx = make_ctx(stub)
    check = BackTranslationCheck()
    for src, tgt in (("Water boils.", "पानी उबलता है।"), ("Ice melts.", "बर्फ पिघलती है।")):
        r = ItemResult(item=Item(id=src, lang="hi", source=src, translation=tgt))
        assert run_check(check, r, ctx)["status"] == "ok"
    assert vars(check) == {}


# ---------------------------------------------------------------------------
# K8: an unlocated signal joins the strongest cluster of its category
# ---------------------------------------------------------------------------

THRESHOLDS = {"omission": 0.75}


def sig(check: str, score: float, span: tuple[int, int] | None = None, **kw) -> Signal:
    return Signal(check=check, category="omission", score=score, evidence=check, span=span, **kw)


def test_k8_unlocated_signal_joins_the_strongest_cluster_not_the_first() -> None:
    item = Item(id="x", lang="hi", source="s" * 80, translation="t" * 80)
    signals = [
        sig("back_translation", 0.7, (10, 20)),  # cluster A: seeded first, score 0.70
        sig("judge", 0.65, (40, 50), details={"replica": 0}),  # cluster B ...
        sig("judge", 0.6, (42, 48), details={"replica": 1}),  # ... corroborated: B = 0.86
        sig("length", 0.3),  # unlocated: "something is missing somewhere"
    ]
    flags, uncertain, _ = fuse(item, signals, THRESHOLDS, margin=0.2)
    assert [f.span for f in flags] == [(40, 50)] or [f.span for f in flags] == [(42, 48)]
    assert "length" in flags[0].checks  # joined B, the strongest
    assert [f.span for f in uncertain] == [(10, 20)]  # A was not pushed over the threshold by it


def test_k8_unlocated_signals_alone_form_one_cluster() -> None:
    item = Item(id="x", lang="hi", source="s" * 40, translation="t" * 40)
    flags, uncertain, below = fuse(item, [sig("length", 0.3), sig("judge", 0.45)], THRESHOLDS, margin=0.2)
    assert len(flags + uncertain + below) == 1
    assert sorted((flags + uncertain + below)[0].checks) == ["judge", "length"]


def test_k8_unlocated_signal_does_not_join_another_category() -> None:
    item = Item(id="x", lang="hi", source="s" * 40, translation="t" * 40)
    signals = [Signal("numbers", "number", 0.9, "n", span=(3, 5)), sig("length", 0.3)]
    flags, uncertain, below = fuse(item, signals, {"number": 0.5, "omission": 0.5}, margin=0.2)
    assert [f.category for f in flags] == ["number"]
    assert [f.category for f in uncertain + below] == ["omission"]  # its own cluster, not merged into "number"


# ---------------------------------------------------------------------------
# K6: transliteration calls deduplicated across the dataset and capped per item
# ---------------------------------------------------------------------------


def test_k6_each_unique_term_is_transliterated_once(start_stub, make_ctx) -> None:
    stub = start_stub({"transliterate": {"hi": {"Newton": "न्यूटन"}}})
    ctx = make_ctx(stub)
    items = [
        Item(id=str(k), lang="hi", source="The laws of Newton describe motion.", translation="न्यूटन के नियम गति बताते हैं।")
        for k in range(6)
    ]
    prefetch_transliterations(items, ctx, concurrency=6)
    results = [ItemResult(item=it) for it in items]
    for r in results:
        assert run_check(EntityCheck(), r, ctx)["status"] == "ok"
    assert [r["path"] for r in stub.requests].count("/transliterate") == 1
    assert all(r.state["entity_renderings"] == {"Newton": "न्यूटन"} for r in results)


def test_k6_direct_transliteration_calls_are_capped_per_item(start_stub, make_ctx, run_one: Callable) -> None:
    stub = start_stub()
    ctx = make_ctx(stub, entity_max_transliterations=2)
    source = "Newton, Einstein, Curie, Darwin and Raman were scientists."
    r = run_one(EntityCheck, ctx, source, "वे सभी वैज्ञानिक थे।")  # no prefetch: the check calls directly
    assert r.checks["entities"]["status"] == "ok"
    assert [q["path"] for q in stub.requests].count("/transliterate") == 2


def test_k6_rejected_transliteration_key_costs_one_request_per_run(start_stub, make_ctx) -> None:
    """Only the three documented keys set: the translate key is refused for transliteration (D29)."""
    stub = start_stub({"transliterate": {"hi": {"Newton": "न्यूटन"}}})
    stub.add_fault("/transliterate", 401, times=-1, code="invalid_api_key_error")
    ctx = make_ctx(stub)
    items = [
        Item(id="1", lang="hi", source="The laws of Newton describe motion.", translation="न्यूटन के नियम गति बताते हैं।"),
        Item(id="2", lang="hi", source="Einstein and Curie were scientists.", translation="वे वैज्ञानिक थे।"),
    ]
    prefetch_transliterations(items, ctx, concurrency=1)
    assert [q["path"] for q in stub.requests] == ["/transliterate"]
    assert len(ctx.transliterations) >= 2  # several terms wanted, one request sent
    assert set(ctx.transliterations.values()) == {""}  # stored as failed: the check uses the back-translation
    for it in items:
        assert run_check(EntityCheck(), ItemResult(item=it), ctx)["status"] == "ok"
    assert [q["path"] for q in stub.requests] == ["/transliterate"]
