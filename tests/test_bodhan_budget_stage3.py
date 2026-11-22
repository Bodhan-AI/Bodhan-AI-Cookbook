import sys
from decimal import Decimal
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO / "examples" / "bodhan_tool_agent"
sys.path.insert(0, str(PACKAGE_ROOT))

from bodhan_agent.budget import UsageTracker  # noqa: E402


def test_transcription_cost():
    tracker = UsageTracker()

    estimate = tracker.record_transcription(120)

    assert estimate.amount_inr == Decimal("0.20")
    assert tracker.bodhan_total_inr == Decimal("0.20")


def test_translation_cost_uses_output_tokens():
    tracker = UsageTracker()

    estimate = tracker.record_translation({"completion_tokens": 5000})

    assert estimate.amount_inr == Decimal("0.10")


def test_translation_supports_output_tokens_key():
    tracker = UsageTracker()

    estimate = tracker.record_translation({"output_tokens": 2500})

    assert estimate.amount_inr == Decimal("0.050")


def test_translation_missing_usage_is_unknown():
    tracker = UsageTracker()

    estimate = tracker.record_translation({})

    assert estimate.amount_inr is None
    assert tracker.calls["translate"] == 1
    assert tracker.unknown_costs


def test_speech_cost_uses_input_characters():
    tracker = UsageTracker()

    estimate = tracker.record_speech("a" * 1000)

    assert estimate.amount_inr == Decimal("0.600")


def test_ocr_cost():
    tracker = UsageTracker()

    estimate = tracker.record_ocr(3)

    assert estimate.amount_inr == Decimal("0.60")


def test_budget_allows_cost_within_limit():
    tracker = UsageTracker()
    tracker.record_ocr(2)

    assert tracker.can_spend(Decimal("0.10"), Decimal("1.00"))


def test_budget_rejects_cost_over_limit():
    tracker = UsageTracker()
    tracker.record_ocr(2)

    assert not tracker.can_spend(Decimal("0.10"), Decimal("0.30"))


def test_report_contains_estimates_and_unknown_costs():
    tracker = UsageTracker()
    tracker.record_transcription(60)
    tracker.record_translation({})

    report = tracker.report()

    assert report["currency"] == "INR"
    assert report["bodhan_total_estimate_inr"] == "0.10"
    assert report["calls"]["transcribe"] == 1
    assert report["calls"]["translate"] == 1
    assert len(report["unknown_costs"]) == 1


@pytest.mark.parametrize("duration", [-1, float("inf"), float("nan")])
def test_invalid_transcription_duration_is_rejected(duration):
    tracker = UsageTracker()

    with pytest.raises(ValueError):
        tracker.record_transcription(duration)


def test_negative_ocr_page_count_is_rejected():
    tracker = UsageTracker()

    with pytest.raises(ValueError):
        tracker.record_ocr(-1)


def test_reservation_is_included_in_budget_boundary():
    tracker = UsageTracker()

    reserved = tracker.reserve(Decimal("0.40"))

    assert reserved == Decimal("0.40")
    assert tracker.reserved_total_inr == Decimal("0.40")
    assert tracker.can_spend(Decimal("0.60"), Decimal("1.00"))
    assert not tracker.can_spend(Decimal("0.61"), Decimal("1.00"))


def test_reconciliation_replaces_reservation_with_actual_cost():
    tracker = UsageTracker()
    reserved = tracker.reserve(Decimal("0.40"))
    actual = tracker.record_speech("a" * 100)

    reconciled = tracker.reconcile(reserved, actual)

    assert reconciled.amount_inr == Decimal("0.060")
    assert tracker.reserved_total_inr == Decimal("0")
    assert tracker.bodhan_total_inr == Decimal("0.060")


def test_unknown_usage_is_reported_once():
    tracker = UsageTracker()

    tracker.mark_unknown(
        "Transcription duration was missing",
        service="transcribe",
    )
    tracker.mark_unknown(
        "Transcription duration was missing",
        service="transcribe",
    )

    report = tracker.report()
    assert report["calls"]["transcribe"] == 0
    assert report["unknown_attempts_by_service"]["transcribe"] == 2
    assert report["unknown_costs"] == [
        "Transcription duration was missing",
    ]


def test_report_distinguishes_reconciled_estimated_reserved_and_unknown():
    tracker = UsageTracker()
    tracker.record_translation({"completion_tokens": 5000})
    tracker.record_speech("a" * 100)
    tracker.record_ocr(1)
    tracker.mark_unknown("Audio duration unavailable", service="transcribe")
    tracker.reserve(Decimal("0.40"))

    report = tracker.report()

    assert Decimal(report["reconciled_bodhan_cost_inr"]) == Decimal("0.100")
    assert Decimal(
        report["reconciled_bodhan_cost_by_service"]["translate"]
    ) == Decimal("0.100")
    assert Decimal(report["estimated_bodhan_cost_inr"]) == Decimal("0.260")
    assert Decimal(
        report["estimated_bodhan_cost_by_service"]["speak"]
    ) == Decimal("0.060")
    assert Decimal(
        report["estimated_bodhan_cost_by_service"]["ocr"]
    ) == Decimal("0.20")
    assert Decimal(report["reserved_bodhan_cost_inr"]) == Decimal("0.40")
    assert report["unknown_bodhan_usage_or_cost"] == [
        "Audio duration unavailable",
    ]


@pytest.mark.parametrize(
    "invalid_usage",
    [
        True,
        False,
        12.5,
        -1,
        "-1",
        "12.5",
        "abc",
        "",
        None,
        [],
        {},
    ],
)
def test_malformed_translation_usage_is_unknown(invalid_usage):
    tracker = UsageTracker()

    estimate = tracker.record_translation(
        {"completion_tokens": invalid_usage}
    )

    assert estimate.amount_inr is None
    assert estimate.reason
    assert tracker.calls["translate"] == 1
    assert tracker.bodhan_total_inr == Decimal("0")