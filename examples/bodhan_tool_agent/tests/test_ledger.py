"""Unit tests for the Rupee Cost Ledger and Circuit Breaker (src.common.ledger)."""

from __future__ import annotations

import pytest
from src.common.ledger import (
    PRICE_OCR_PER_PAGE_INR,
    PRICE_SPEAK_PER_10K_CHARS_INR,
    PRICE_TRANSCRIBE_PER_MIN_INR,
    PRICE_TRANSLATE_PER_10K_TOKENS_INR,
    CostLedger,
)


def test_cost_calculation():
    # ASR: 60 seconds = ₹0.10
    assert CostLedger.calculate_cost("indic-transcribe", 60.0) == 0.10
    # ASR: 30 seconds = ₹0.05
    assert CostLedger.calculate_cost("indic-transcribe", 30.0) == 0.05

    # MT: 10,000 output tokens = ₹0.20
    assert CostLedger.calculate_cost("indic-translate", 10000) == 0.20
    # MT: 5,000 output tokens = ₹0.10
    assert CostLedger.calculate_cost("indic-translate", 5000) == 0.10

    # TTS: 10,000 chars = ₹6.00
    assert CostLedger.calculate_cost("indic-speak", 10000) == 6.00
    # TTS: 500 chars = ₹0.30
    assert CostLedger.calculate_cost("indic-speak", 500) == 0.30

    # OCR: 5 pages = 5 * ₹0.20 = ₹1.00
    assert CostLedger.calculate_cost("indic-ocr", 5) == 1.00


def test_ledger_budget_enforcement():
    ledger = CostLedger(max_budget_inr=1.0)
    assert ledger.remaining_budget_inr == 1.0
    assert not ledger.is_exhausted

    # Can afford 4 pages of OCR (4 * 0.20 = 0.80)
    assert ledger.can_afford("indic-ocr", 4)
    # Cannot afford 6 pages of OCR (6 * 0.20 = 1.20)
    assert not ledger.can_afford("indic-ocr", 6)

    # Record 4 pages
    cost = ledger.record_operation("indic-ocr", units=4, step=1, details="4 pages scanned")
    assert cost == 0.80
    assert ledger.cumulative_cost_inr == 0.80
    assert ledger.remaining_budget_inr == 0.20
    assert not ledger.is_exhausted

    # Now cannot afford another 2 pages (0.40 > 0.20 remaining)
    assert not ledger.can_afford("indic-ocr", 2)
    # Can afford 1 page (0.20 <= 0.20)
    assert ledger.can_afford("indic-ocr", 1)

    ledger.record_operation("indic-ocr", units=1, step=2, details="1 page scanned")
    assert ledger.cumulative_cost_inr == 1.00
    assert ledger.is_exhausted
    assert not ledger.can_afford("indic-ocr", 1)


def test_ledger_zero_budget():
    """Verify that a zero-budget ledger halts immediately and disallows all paid operations."""
    ledger = CostLedger(max_budget_inr=0.0)
    assert ledger.max_budget_inr == 0.0
    assert ledger.cumulative_cost_inr == 0.0
    assert ledger.remaining_budget_inr == 0.0
    assert ledger.is_exhausted is True

    # Free / 0-unit operations fit within zero budget
    assert ledger.can_afford("indic-ocr", 0) is True
    # Any positive-unit paid operation is immediately rejected
    assert ledger.can_afford("indic-ocr", 1) is False
    assert ledger.can_afford("indic-transcribe", 1.0) is False
    assert ledger.can_afford("indic-translate", 100) is False
    assert ledger.can_afford("indic-speak", 10) is False

    # Recording an operation still updates cumulative spend and keeps is_exhausted True
    cost = ledger.record_operation("indic-ocr", units=1, step=1, details="Zero budget overage")
    assert cost == 0.20
    assert ledger.cumulative_cost_inr == 0.20
    assert ledger.remaining_budget_inr == 0.0
    assert ledger.is_exhausted is True


def test_negative_units_rejection():
    """Verify that negative units raise ValueError across all models and methods."""
    # Static calculation rejection
    for model in ["indic-transcribe", "indic-translate", "indic-speak", "indic-ocr", "llm"]:
        with pytest.raises(ValueError, match="Units cannot be negative"):
            CostLedger.calculate_cost(model, -1.0)

    ledger = CostLedger(max_budget_inr=10.0)
    # can_afford rejection
    with pytest.raises(ValueError, match="Units cannot be negative"):
        ledger.can_afford("indic-ocr", -5)

    # record_operation rejection (prevents fraudulent negative charges that restore budget)
    with pytest.raises(ValueError, match="Units cannot be negative"):
        ledger.record_operation("indic-ocr", units=-5, step=1)
    assert ledger.cumulative_cost_inr == 0.0


def test_unknown_model_handling():
    """Verify that unknown model identifiers are rejected instead of billed as free."""
    with pytest.raises(ValueError, match="Unsupported cost model"):
        CostLedger.calculate_cost("unknown-model", 100)
    with pytest.raises(ValueError, match="Unsupported cost model"):
        CostLedger.calculate_cost("gpt-nonexistent", 50)

    ledger = CostLedger(max_budget_inr=5.0)
    with pytest.raises(ValueError, match="Unsupported cost model"):
        ledger.can_afford("unknown-model", 100)
    with pytest.raises(ValueError, match="Unsupported cost model"):
        ledger.record_operation("unknown-model", units=100, step=1, details="Unknown model")

    assert ledger.cumulative_cost_inr == 0.0
    assert ledger.history == []


def test_fractional_precision():
    """Verify that fractional units maintain exact 4-decimal INR precision without floating point drift."""
    # 1.2345 seconds ASR: (1.2345 / 60) * 0.10 = 0.0020575 -> rounded to 0.0021
    assert CostLedger.calculate_cost("indic-transcribe", 1.2345) == 0.0021

    # 333 tokens MT: (333 / 10000) * 0.20 = 0.00666 -> rounded to 0.0067
    assert CostLedger.calculate_cost("indic-translate", 333) == 0.0067

    # 15 characters TTS: (15 / 10000) * 6.00 = 0.009
    assert CostLedger.calculate_cost("indic-speak", 15) == 0.009

    # Cumulative additions do not suffer floating-point drift
    ledger = CostLedger(max_budget_inr=10.0)
    for i in range(10):
        ledger.record_operation("indic-transcribe", units=1.2345, step=i + 1)
    # 10 * 0.0021 = 0.0210
    assert ledger.cumulative_cost_inr == 0.0210
    assert ledger.remaining_budget_inr == 9.9790

