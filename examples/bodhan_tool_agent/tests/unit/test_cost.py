"""Tests for cost estimation functions and CostMeter in bodhan_core.cost."""
import pytest

try:
    from bodhan_core.cost import (
        CostMeter,
        estimate_ocr,
        estimate_speak,
        estimate_transcribe,
        estimate_translate,
    )
    _HAVE_COST = True
except ImportError:
    _HAVE_COST = False

pytestmark = pytest.mark.skipif(not _HAVE_COST, reason="bodhan_core.cost not available")


# ---------------------------------------------------------------------------
# Estimation functions
# ---------------------------------------------------------------------------

def test_estimate_transcribe_60s():
    """60 seconds of audio = 1 minute = 0.10 INR."""
    result = estimate_transcribe(60.0)
    assert abs(result - 0.10) < 1e-9, f"Expected 0.10, got {result}"


def test_estimate_transcribe_zero():
    assert estimate_transcribe(0.0) == 0.0


def test_estimate_translate_10k_tokens():
    """10 000 output tokens = 0.20 INR."""
    result = estimate_translate(10_000)
    assert abs(result - 0.20) < 1e-9, f"Expected 0.20, got {result}"


def test_estimate_translate_zero():
    assert estimate_translate(0) == 0.0


def test_estimate_speak_10k_chars():
    """10 000 input characters = 6.0 INR."""
    result = estimate_speak(10_000)
    assert abs(result - 6.0) < 1e-9, f"Expected 6.0, got {result}"


def test_estimate_speak_zero():
    assert estimate_speak(0) == 0.0


def test_estimate_ocr_one_page():
    """1 page = 0.20 INR."""
    result = estimate_ocr(1)
    assert abs(result - 0.20) < 1e-9, f"Expected 0.20, got {result}"


def test_estimate_ocr_zero():
    assert estimate_ocr(0) == 0.0


# ---------------------------------------------------------------------------
# CostMeter
# ---------------------------------------------------------------------------

def test_cost_meter_add_accumulates():
    meter = CostMeter()
    meter.add("transcribe", 0.10)
    meter.add("translate", 0.20)
    assert abs(meter.total_inr - 0.30) < 1e-9


def test_cost_meter_add_records_entries():
    meter = CostMeter()
    meter.add("transcribe", 0.10, cached=False)
    meter.add("ocr", 0.20, cached=True)
    assert len(meter.per_call) == 2
    assert meter.per_call[0]["tool"] == "transcribe"
    assert meter.per_call[1]["cached"] is True


def test_cost_meter_check_budget_ok():
    meter = CostMeter()
    meter.add("transcribe", 1.0)
    # estimated 0.5 INR, remaining budget 2.0 → should be fine
    assert meter.check_budget(0.5, remaining=2.0) is True


def test_cost_meter_check_budget_over():
    meter = CostMeter()
    meter.add("transcribe", 1.0)
    # estimated 3.0 INR, remaining budget 2.0 → over budget
    assert meter.check_budget(3.0, remaining=2.0) is False


def test_cost_meter_check_budget_exactly_equal():
    meter = CostMeter()
    # estimated equals remaining exactly — should be ok (not over)
    assert meter.check_budget(1.0, remaining=1.0) is True


def test_cost_meter_summary_contains_total():
    meter = CostMeter()
    meter.add("speak", 6.0)
    summary = meter.summary()
    assert "total_inr" in summary
    assert abs(summary["total_inr"] - 6.0) < 1e-9
