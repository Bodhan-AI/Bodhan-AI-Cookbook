"""Tests for budget tracking and cost limits enforcement."""

import unittest
from bodhan_agent.budget import BudgetExceededException, BudgetTracker


class TestBudget(unittest.TestCase):
    def test_step_budget_limit(self):
        tracker = BudgetTracker(max_steps=3, max_cost_inr=10.0)
        tracker.record_step(cost_inr=0.01)
        tracker.record_step(cost_inr=0.01)
        tracker.record_step(cost_inr=0.01)

        with self.assertRaises(BudgetExceededException) as ctx:
            tracker.record_step(cost_inr=0.01)
        self.assertIn("Step limit exceeded", str(ctx.exception))

    def test_cost_budget_limit(self):
        tracker = BudgetTracker(max_steps=50, max_cost_inr=1.0)
        tracker.record_step(cost_inr=0.5)
        self.assertTrue(tracker.can_proceed())

        with self.assertRaises(BudgetExceededException) as ctx:
            tracker.record_step(cost_inr=0.6)
        self.assertIn("Cost budget exceeded", str(ctx.exception))
        self.assertFalse(tracker.can_proceed())


if __name__ == "__main__":
    unittest.main()
