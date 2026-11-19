"""Unit tests for agent budget tracking and limit enforcement."""
from __future__ import annotations

import time
from unittest.mock import patch
import pytest

from bodhan_agent.budget import BudgetTracker, BudgetExceeded


def test_budget_steps_cap():
    """Exceeding max_steps triggers BudgetExceeded."""
    tracker = BudgetTracker(max_steps=3, max_cost_inr=10.0)

    # 3 allowed steps
    tracker.check()
    tracker.record_step(cost_inr=0.10)
    tracker.check()
    tracker.record_step(cost_inr=0.10)
    tracker.check()
    tracker.record_step(cost_inr=0.10)

    # 4th step should exceed budget
    with pytest.raises(BudgetExceeded) as exc_info:
        tracker.check()
    assert "Max steps (3) reached" in str(exc_info.value)


def test_budget_cost_cap_pre_call():
    """Cost cap triggers BudgetExceeded before making an expensive call."""
    tracker = BudgetTracker(max_steps=10, max_cost_inr=5.0)

    tracker.record_step(cost_inr=4.0)
    assert tracker.spent_inr == 4.0

    # Call with estimate 0.5 is fine (4.0 + 0.5 <= 5.0)
    tracker.check(estimated_cost=0.5)

    # Call with estimate 1.5 would exceed 5.0 (4.0 + 1.5 > 5.0)
    with pytest.raises(BudgetExceeded) as exc_info:
        tracker.check(estimated_cost=1.5)
    assert "Cost budget" in str(exc_info.value)


def test_budget_time_cap():
    """Wall-clock time limit triggers BudgetExceeded."""
    t = BudgetTracker(max_steps=10, max_cost_inr=10.0, max_time_s=60.0)
    # Shift start time back by 70 seconds to exceed 60s limit
    t._start = time.time() - 70.0
    with pytest.raises(BudgetExceeded) as exc_info:
        t.check()
    assert "Time limit" in str(exc_info.value)


def test_budget_partial_spend_tracking():
    """Partial spend and step counts are tracked accurately."""
    tracker = BudgetTracker(max_steps=10, max_cost_inr=10.0)
    tracker.record_step(0.25)
    tracker.record_step(0.75)
    tracker.record_step(1.50)

    assert tracker.steps_used == 3
    assert abs(tracker.spent_inr - 2.50) < 1e-5


def test_budget_parallel_reservation_prevents_overshoot():
    """Reserving budget for parallel steps detects overshoot before execution."""
    tracker = BudgetTracker(max_steps=10, max_cost_inr=2.0)
    tracker.record_step(1.20)

    # Two parallel calls each estimated at 0.50 -> 1.00 total
    # First reservation check (0.50) passes: 1.20 + 0.50 <= 2.0
    tracker.reserve(0.50)

    # Combined reservation (0.90) fails: 1.20 + 0.90 > 2.0
    with pytest.raises(BudgetExceeded):
        tracker.reserve(0.90)
