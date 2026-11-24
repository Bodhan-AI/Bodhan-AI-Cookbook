"""
Unit tests for CostLedger and BudgetGuard.
"""

import pytest

from bodhan_dub.gateway.cost import BudgetExceededError, BudgetGuard, CostLedger


@pytest.mark.asyncio
async def test_cost_calculation():
    ledger = CostLedger(
        cost_asr_per_min=0.10,
        cost_translate_per_10k=0.20,
        cost_tts_per_10k=6.00,
    )

    # Transcribe: 60s (1 min) = ₹0.10
    cost_asr = await ledger.record_transcribe(60.0)
    assert cost_asr == 0.10

    # Translate: 10,000 tokens = ₹0.20
    cost_trans = await ledger.record_translate(10000)
    assert cost_trans == 0.20

    # Speak: 10,000 chars = ₹6.00
    cost_tts = await ledger.record_speak(10000)
    assert cost_tts == 6.00

    total = await ledger.get_total_inr()
    assert total == 6.30


@pytest.mark.asyncio
async def test_cache_hits_cost_zero():
    ledger = CostLedger()
    cost_hit = await ledger.record_transcribe(60.0, from_cache=True)
    assert cost_hit == 0.0

    total = await ledger.get_total_inr()
    assert total == 0.0
    summary = await ledger.get_summary()
    assert summary["cache_hits"] == 1
    assert summary["cost_saved_inr"] > 0.0


@pytest.mark.asyncio
async def test_budget_guard_blocks_excess():
    ledger = CostLedger()
    guard = BudgetGuard(ledger, max_cost_inr=5.00)

    # Initial check passes
    await guard.check_budget(estimated_next_cost=2.00)

    # Record spend of 4.50
    await ledger.record_speak(7500)  # 7500 * (6 / 10000) = 4.50

    # Next call estimating 1.00 should raise BudgetExceededError (4.50 + 1.00 > 5.00)
    with pytest.raises(BudgetExceededError, match="Budget limit exceeded"):
        await guard.check_budget(estimated_next_cost=1.00)
