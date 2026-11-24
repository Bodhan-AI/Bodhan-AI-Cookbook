from __future__ import annotations

import pytest

from bodhan_dub import cost
from bodhan_dub.cost import CostMeter


def test_published_rates() -> None:
    assert cost.transcribe_cost_inr(60.0) == pytest.approx(0.10)
    assert cost.translate_cost_inr(10_000) == pytest.approx(0.20)
    assert cost.speak_cost_inr(10_000) == pytest.approx(6.00)


def test_partial_units_are_proportional() -> None:
    assert cost.transcribe_cost_inr(30.0) == pytest.approx(0.05)
    assert cost.speak_cost_inr(2_500) == pytest.approx(1.50)
    assert cost.translate_cost_inr(5_000) == pytest.approx(0.10)


def test_negative_input_is_clamped_to_zero() -> None:
    assert cost.transcribe_cost_inr(-10.0) == 0.0
    assert cost.speak_cost_inr(-5) == 0.0


def test_meter_accumulates_across_stages() -> None:
    meter = CostMeter()
    meter.charge_transcribe(120.0)
    meter.charge_translate(10_000, stage="translate")
    meter.charge_speak(5_000, stage="speak")

    summary = meter.summary()

    assert summary["total_inr"] == pytest.approx(0.20 + 0.20 + 3.00)
    assert summary["by_stage_inr"]["asr"] == pytest.approx(0.20)
    assert summary["by_stage_inr"]["speak"] == pytest.approx(3.00)
    assert summary["calls"] == 3


def test_cached_calls_cost_nothing_but_are_counted() -> None:
    meter = CostMeter()
    meter.charge_speak(10_000, cached=True)

    summary = meter.summary()

    assert summary["total_inr"] == 0.0
    assert summary["cached_calls"] == 1
    assert summary["characters"] == 10_000


def test_units_are_reported() -> None:
    meter = CostMeter()
    meter.charge_transcribe(90.0)
    meter.charge_translate(2_000)

    summary = meter.summary()

    assert summary["audio_seconds"] == pytest.approx(90.0)
    assert summary["output_tokens"] == 2_000
