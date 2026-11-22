"""Unit tests for the budget controller."""

import time

import pytest

from bodhan_agent.budget import BudgetController, BudgetExhausted


@pytest.mark.unit
class TestBudgetController:
    def test_initial_state(self):
        bc = BudgetController(max_steps=10, max_cost_inr=5.0)
        assert bc.current_step == 0
        assert bc.total_cost_inr == 0.0
        assert bc.max_steps == 10
        assert bc.max_cost_inr == 5.0

    def test_record_step(self):
        bc = BudgetController(max_steps=10, max_cost_inr=5.0)
        bc.record_step(tool="test_tool", cost_inr=0.5, latency_seconds=1.0, details="test")
        assert bc.current_step == 1
        assert bc.total_cost_inr == 0.5
        assert len(bc.step_log) == 1

    def test_step_limit_exceeded(self):
        bc = BudgetController(max_steps=2, max_cost_inr=100.0)
        bc.record_step(tool="a", cost_inr=0.1)
        bc.record_step(tool="b", cost_inr=0.1)
        with pytest.raises(BudgetExhausted) as exc_info:
            bc.check()
        assert exc_info.value.budget_type == "steps"

    def test_cost_limit_exceeded(self):
        bc = BudgetController(max_steps=100, max_cost_inr=1.0)
        bc.record_step(tool="expensive", cost_inr=1.5)
        with pytest.raises(BudgetExhausted) as exc_info:
            bc.check()
        assert exc_info.value.budget_type == "cost"

    def test_timeout_exceeded(self):
        bc = BudgetController(max_steps=100, max_cost_inr=100.0, timeout_seconds=0.01)
        time.sleep(0.02)
        with pytest.raises(BudgetExhausted) as exc_info:
            bc.check()
        assert exc_info.value.budget_type == "timeout"

    def test_can_afford(self):
        bc = BudgetController(max_cost_inr=1.0)
        assert bc.can_afford(0.5) is True
        bc.record_step(tool="a", cost_inr=0.8)
        assert bc.can_afford(0.5) is False
        assert bc.can_afford(0.1) is True

    def test_remaining(self):
        bc = BudgetController(max_steps=10, max_cost_inr=5.0)
        bc.record_step(tool="a", cost_inr=1.0)
        rem = bc.remaining()
        assert rem["steps_remaining"] == 9
        assert rem["cost_remaining_inr"] == pytest.approx(4.0, abs=0.01)

    def test_summary(self):
        bc = BudgetController(max_steps=10, max_cost_inr=5.0)
        bc.record_step(tool="a", cost_inr=1.0, details="first")
        bc.record_step(tool="b", cost_inr=0.5, details="second")
        s = bc.summary()
        assert s["total_steps"] == 2
        assert s["total_cost_inr"] == 1.5
        assert len(s["step_log"]) == 2

    def test_check_passes_within_budget(self):
        bc = BudgetController(max_steps=10, max_cost_inr=5.0, timeout_seconds=60)
        bc.record_step(tool="a", cost_inr=0.1)
        bc.check()  # Should not raise
