import pytest

from bodhan_mcp.cost import (
    BudgetExceeded, CostMeter, ocr_cost, stt_cost, translate_cost, tts_cost,
)


def test_prices_match_the_assignment():
    assert stt_cost(60) == pytest.approx(0.10)
    assert translate_cost(10_000) == pytest.approx(0.20)
    assert tts_cost(10_000) == pytest.approx(6.0)
    assert ocr_cost(8) == pytest.approx(1.6)


def test_meter_totals_and_remaining():
    meter = CostMeter(limit_inr=5.0)
    meter.add("ocr", ocr_cost(8), "8 pages")
    assert meter.total == pytest.approx(1.6)
    assert meter.remaining == pytest.approx(3.4)


def test_meter_blocks_call_that_would_exceed():
    meter = CostMeter(limit_inr=1.0)
    meter.add("tts", 0.9)
    assert meter.would_exceed(0.2)
    with pytest.raises(BudgetExceeded):
        meter.check(0.2)
    meter.check(0.1)  # exactly fits: no error


def test_meter_without_limit_never_blocks():
    meter = CostMeter()
    meter.add("tts", 100.0)
    assert meter.remaining is None
    meter.check(1000.0)
