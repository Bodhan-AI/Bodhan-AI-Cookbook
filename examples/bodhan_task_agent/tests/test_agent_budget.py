from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from bodhan_agent.core import budget
from bodhan_agent.schemas.budget import MinimumCost, Quote, Reservation
from bodhan_agent.schemas.ledger import Artifact

ENGLISH = "Plants make food from sunlight. "


def quote(amount: float, tool: str = "ocr_document") -> Quote:
    return Quote(tool=tool, amount_inr=amount, bound="upper")


def source(kind: str, **fields: Any) -> Artifact:
    return Artifact(id="f1", path=f"/in/file.{kind}", kind=kind, producer="user_input", step_status="ok", **fields)


def test_quote_parts_must_add_up() -> None:
    with pytest.raises(ValidationError, match="add up"):
        Quote(tool="t", amount_inr=1.0, bound="upper", parts={"speak": 0.5})


def test_quote_rejects_negative_values() -> None:
    with pytest.raises(ValidationError):
        Quote(tool="t", amount_inr=-0.1, bound="exact")
    with pytest.raises(ValidationError, match="negative"):
        Quote(tool="t", amount_inr=1.0, bound="upper", parts={"a": 2.0, "b": -1.0})


def test_ocr_quote_per_page() -> None:
    result = budget.quote_ocr(8)
    assert (result.amount_inr, result.bound, result.parts) == (1.6, "exact", {"ocr": 1.6})


def test_ocr_quote_capped_at_server_page_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BODHAN_OCR_MAX_PAGES", "5")
    assert budget.quote_ocr(50).amount_inr == pytest.approx(1.0)


def test_ocr_quote_unknown_pages_is_assumed_not_zero() -> None:
    result = budget.quote_ocr(None)
    assert result.bound == "assumed" and result.amount_inr == pytest.approx(4.0)
    assert "page count unknown" in result.notes[0]


def test_transcribe_quote_per_minute() -> None:
    result = budget.quote_transcribe(180)
    assert (result.amount_inr, result.bound) == (pytest.approx(0.3), "upper")


@pytest.mark.parametrize("duration", [None, float("nan")])
def test_transcribe_quote_unknown_length_uses_cap(duration: float | None) -> None:
    result = budget.quote_transcribe(duration)
    assert result.bound == "assumed" and result.amount_inr == pytest.approx(3.0)


def test_translate_quote_counts_server_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BODHAN_TRANSLATE_CHUNK_CHARS", "2000")
    text = ENGLISH * 400
    requests = budget.translate_requests(text)
    assert requests == 7
    result = budget.quote_translate(text)
    assert result.amount_inr == pytest.approx(7 * 4096 * 0.20 / 10_000)
    assert result.bound == "assumed"


def test_translate_five_chunks_matches_review_example(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(budget, "translate_requests", lambda text: 5)
    assert budget.quote_translate("x").amount_inr == pytest.approx(0.4096)


def test_translate_empty_text_is_free() -> None:
    assert budget.quote_translate("   ").amount_inr == 0.0


def test_speech_quote_includes_verification() -> None:
    text = "வணக்கம். " * 100
    with_check = budget.quote_speech(text)
    without_check = budget.quote_speech(text, verify=False)
    assert set(with_check.parts) == {"speak", "transcribe"}
    assert set(without_check.parts) == {"speak"}
    assert with_check.parts["speak"] == without_check.amount_inr
    assert with_check.amount_inr > without_check.amount_inr


def test_speech_quote_bills_markdown_free_characters() -> None:
    plain = budget.quote_speech("Hello world", verify=False).amount_inr
    marked = budget.quote_speech("## **Hello** world", verify=False).amount_inr
    assert marked == plain


@pytest.mark.parametrize("tool", sorted(budget.FREE_TOOLS))
def test_local_tools_are_free(tool: str) -> None:
    assert budget.quote_tool(tool, {}).amount_inr == 0.0


def test_unknown_tool_never_priced_as_zero() -> None:
    with pytest.raises(ValueError, match="unknown tool"):
        budget.quote_tool("mystery", {})


def test_quote_tool_ocr_uses_pages_argument() -> None:
    assert budget.quote_tool("ocr_document", {"pages": "1-3,5"}, source("pdf", pages=8)).amount_inr == pytest.approx(
        0.8
    )


def test_quote_tool_ocr_uses_source_pages_and_images() -> None:
    assert budget.quote_tool("ocr_document", {}, source("pdf", pages=8)).amount_inr == pytest.approx(1.6)
    assert budget.quote_tool("ocr_document", {"pages": " "}, source("image")).amount_inr == pytest.approx(0.2)
    assert budget.quote_tool("ocr_document", {}, None).bound == "assumed"


def test_quote_tool_transcribe_uses_duration() -> None:
    assert budget.quote_tool("transcribe_audio", {}, source("audio", duration_s=60)).amount_inr == pytest.approx(0.1)


def test_quote_tool_translate_reads_inline_or_file(tmp_path: Path) -> None:
    path = tmp_path / "summary.txt"
    path.write_text(ENGLISH)
    inline = budget.quote_tool("translate_text", {"text": ENGLISH})
    from_file = budget.quote_tool("translate_text", {"text_path": str(path)})
    assert inline.amount_inr == from_file.amount_inr > 0


def test_quote_tool_unreadable_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="could not be read"):
        budget.quote_tool("translate_text", {"text_path": str(tmp_path / "missing.txt")})
    with pytest.raises(ValueError, match="give text or text_path"):
        budget.quote_tool("synthesize_speech", {})


def test_quote_tool_speech_respects_verify_flag() -> None:
    assert set(budget.quote_tool("synthesize_speech", {"text": "Hello", "verify": False}).parts) == {"speak"}
    assert set(budget.quote_tool("synthesize_speech", {"text": "Hello"}).parts) == {"speak", "transcribe"}


def test_quote_tool_subtitles_cover_batch_and_fallback(tmp_path: Path) -> None:
    path = tmp_path / "segments.json"
    path.write_text(json.dumps({"segments": [{"text": "one"}, {"text": ""}, {"text": "two"}, {"text": "three"}]}))
    result = budget.quote_tool("make_subtitles", {"segments_path": str(path)})
    assert result.tool == "make_subtitles"
    assert result.amount_inr == pytest.approx((1 + 3) * 4096 * 0.20 / 10_000)


def test_quote_tool_subtitles_accepts_plain_list(tmp_path: Path) -> None:
    path = tmp_path / "segments.json"
    path.write_text(json.dumps([{"text": "one"}]))
    assert budget.quote_tool("make_subtitles", {"segments_path": str(path)}).amount_inr > 0


def test_quote_tool_subtitles_bad_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "segments.json"
    path.write_text("not json")
    with pytest.raises(ValueError, match="segments file"):
        budget.quote_tool("make_subtitles", {"segments_path": str(path)})


def test_minimum_cost_counts_known_inputs_only() -> None:
    pdf = Artifact(id="f1", path="/in/a.pdf", kind="pdf", producer="user_input", pages=8, step_status="ok")
    image = Artifact(id="f2", path="/in/b.png", kind="image", producer="user_input", step_status="ok")
    wav = Artifact(id="f3", path="/in/c.wav", kind="audio", producer="user_input", step_status="ok")
    broken = Artifact(id="f4", path="/in/d.pdf", kind="pdf", producer="user_input", pages=3, step_status="failed")
    result = budget.minimum_cost([pdf, image, wav, broken], {"ocr_document", "transcribe_audio", "synthesize_speech"})
    assert result.known_inr == pytest.approx(1.8)
    assert result.items == {"f1 ocr": 1.6, "f2 ocr": 0.2}
    assert result.unknown == [
        "transcription of f3 (length unknown)",
        "speech synthesis (length depends on earlier steps)",
    ]


def test_minimum_cost_ignores_tools_not_in_plan() -> None:
    pdf = Artifact(id="f1", path="/in/a.pdf", kind="pdf", producer="user_input", pages=8, step_status="ok")
    assert budget.minimum_cost([pdf], {"translate_text"}) == MinimumCost(
        known_inr=0.0, unknown=["translation (length depends on earlier steps)"]
    )


def test_manager_rejects_bad_limit() -> None:
    for bad in (-1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            budget.BudgetManager(bad)


async def test_reserve_and_remaining() -> None:
    manager = budget.BudgetManager(1.0)
    reservation = await manager.reserve(quote(0.4))
    assert isinstance(reservation, Reservation) and reservation.id == "R1"
    assert (manager.reserved_inr, manager.spent_inr, manager.remaining_inr) == (0.4, 0.0, 0.6)
    assert manager.open_reservations == [reservation]


async def test_request_over_remaining_denied() -> None:
    manager = budget.BudgetManager(0.5)
    assert await manager.reserve(quote(0.6)) is None
    assert manager.reserved_inr == 0.0


async def test_exact_fit_allowed() -> None:
    manager = budget.BudgetManager(0.6)
    assert await manager.reserve(quote(0.2)) is not None
    assert await manager.reserve(quote(0.4)) is not None
    assert manager.remaining_inr == 0.0


async def test_zero_budget_still_allows_free_tools() -> None:
    manager = budget.BudgetManager(0.0)
    assert await manager.reserve(budget.quote_free("read")) is not None
    assert await manager.reserve(quote(0.2)) is None


async def test_concurrent_reservations_cannot_overbook() -> None:
    manager = budget.BudgetManager(0.30)
    results = await asyncio.gather(*(manager.reserve(quote(0.2)) for _ in range(5)))
    assert sum(result is not None for result in results) == 1
    assert manager.remaining_inr == pytest.approx(0.1)


async def test_settle_releases_unused_reservation() -> None:
    manager = budget.BudgetManager(2.0)
    reservation = await manager.reserve(quote(1.0))
    charged = await manager.settle(reservation, 0.4)
    assert charged == 0.4
    assert (manager.spent_inr, manager.reserved_inr, manager.remaining_inr) == (0.4, 0.0, 1.6)


async def test_uncertain_outcome_keeps_full_reservation() -> None:
    manager = budget.BudgetManager(2.0)
    reservation = await manager.reserve(quote(1.0))
    assert await manager.settle(reservation, 0.1, uncertain=True) == 1.0
    assert manager.spent_inr == 1.0 and manager.billing_uncertain is True


async def test_unknown_charge_keeps_full_reservation() -> None:
    manager = budget.BudgetManager(2.0)
    reservation = await manager.reserve(quote(1.0))
    assert await manager.settle(reservation, None) == 1.0
    assert manager.billing_uncertain is True


async def test_actual_above_reservation_is_recorded_truthfully() -> None:
    manager = budget.BudgetManager(2.0)
    reservation = await manager.reserve(quote(0.2))
    assert await manager.settle(reservation, 0.4) == 0.4
    assert manager.spent_inr == 0.4


async def test_cache_hit_costs_nothing() -> None:
    manager = budget.BudgetManager(1.0)
    reservation = await manager.reserve(quote(0.6))
    assert await manager.settle(reservation, 0.0, cache_hits=3) == 0.0
    assert (manager.spent_inr, manager.remaining_inr, manager.cache_hits) == (0.0, 1.0, 3)


async def test_out_of_order_settlement() -> None:
    manager = budget.BudgetManager(3.0)
    first = await manager.reserve(quote(1.0))
    second = await manager.reserve(quote(1.5))
    await manager.settle(second, 0.5)
    assert manager.reserved_inr == 1.0
    await manager.settle(first, 0.25)
    assert (manager.spent_inr, manager.reserved_inr) == (0.75, 0.0)


async def test_settle_tracks_possible_billing() -> None:
    manager = budget.BudgetManager(1.0)
    reservation = await manager.reserve(quote(0.2))
    await manager.settle(reservation, 0.2, possibly_billed_failed_attempts=2)
    assert manager.possibly_billed_failed_attempts == 2


async def test_settle_twice_rejected() -> None:
    manager = budget.BudgetManager(1.0)
    reservation = await manager.reserve(quote(0.2))
    await manager.settle(reservation, 0.2)
    with pytest.raises(ValueError, match="not open"):
        await manager.settle(reservation, 0.2)


@pytest.mark.parametrize("bad", [-0.1, float("nan")])
async def test_settle_rejects_bad_charge(bad: float) -> None:
    manager = budget.BudgetManager(1.0)
    reservation = await manager.reserve(quote(0.2))
    with pytest.raises(ValueError, match="charged_inr"):
        await manager.settle(reservation, bad)
    assert manager.reserved_inr == 0.2


async def test_release_frees_without_charge() -> None:
    manager = budget.BudgetManager(1.0)
    reservation = await manager.reserve(quote(0.6))
    await manager.release(reservation)
    assert (manager.spent_inr, manager.remaining_inr) == (0.0, 1.0)
    with pytest.raises(ValueError, match="not open"):
        await manager.release(reservation)


async def test_cost_source_reports_spent_not_reservations() -> None:
    manager = budget.BudgetManager(2.0)
    reservation = await manager.reserve(quote(1.0))
    assert manager.cost_source() == 0.0
    await manager.settle(reservation, 0.3)
    assert manager.cost_source() == 0.3


async def test_paid_preflight_translation_counts_toward_total() -> None:
    manager = budget.BudgetManager(1.0)
    translation = await manager.reserve(budget.quote_translate("इस अध्याय का सारांश", tool="translate_text"))
    await manager.settle(translation, 0.001)
    ocr = await manager.reserve(budget.quote_ocr(1))
    await manager.settle(ocr, 0.2)
    assert manager.spent_inr == pytest.approx(0.201)
