"""Unit tests for BudgetManager."""
from __future__ import annotations

import pytest

from bodhan_agent.budget import BudgetExceeded, BudgetManager


def test_initial_state() -> None:
    bm = BudgetManager(max_steps=10, max_cost_inr=5.0)
    assert bm.steps_used == 0
    assert bm.cost_used == 0.0


def test_can_proceed_when_within_budget() -> None:
    bm = BudgetManager(max_steps=10, max_cost_inr=5.0)
    assert bm.can_proceed(estimated_cost=1.0) is True


def test_cannot_proceed_when_steps_exhausted() -> None:
    bm = BudgetManager(max_steps=2, max_cost_inr=100.0)
    bm.record_step(0.1)
    bm.record_step(0.1)
    assert bm.can_proceed() is False


def test_cannot_proceed_when_cost_would_exceed() -> None:
    bm = BudgetManager(max_steps=100, max_cost_inr=1.0)
    bm.record_step(0.8)
    assert bm.can_proceed(estimated_cost=0.5) is False


def test_assert_raises_when_budget_exceeded() -> None:
    bm = BudgetManager(max_steps=1, max_cost_inr=1.0)
    bm.record_step(0.5)
    with pytest.raises(BudgetExceeded):
        bm.assert_can_proceed()


def test_summary_keys() -> None:
    bm = BudgetManager(max_steps=5, max_cost_inr=2.0)
    bm.record_step(0.3)
    s = bm.summary()
    assert s["steps_used"] == 1
    assert s["cost_limit_inr"] == 2.0
    assert s["cost_used_inr"] == pytest.approx(0.3)
