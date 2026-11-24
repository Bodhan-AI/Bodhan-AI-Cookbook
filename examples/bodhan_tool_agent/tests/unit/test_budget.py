"""Unit tests for budget management, cost accumulation, and step limits."""

from __future__ import annotations

import pytest
from bodhan_agent.budget import BudgetManager
from bodhan_mcp.errors import BudgetExceededError


def test_budget_step_limit_enforced():
    bm = BudgetManager(max_steps=2, max_cost_inr=10.0)
    bm.check_can_execute_step(1.0)
    bm.record_step_execution(1.0)
    assert bm.current_steps == 1

    bm.check_can_execute_step(1.0)
    bm.record_step_execution(1.0)
    assert bm.current_steps == 2

    # 3rd step must fail
    with pytest.raises(BudgetExceededError) as exc_info:
        bm.check_can_execute_step(1.0)
    assert "Maximum allowed steps" in str(exc_info.value)


def test_budget_cost_limit_enforced():
    bm = BudgetManager(max_steps=10, max_cost_inr=5.0)
    bm.check_can_execute_step(3.0)
    bm.record_step_execution(3.0)
    assert bm.remaining_budget_inr == 2.0

    # Step requiring 3.0 exceeds 2.0 remaining
    with pytest.raises(BudgetExceededError) as exc_info:
        bm.check_can_execute_step(3.0)
    assert "exceed remaining budget" in str(exc_info.value)
