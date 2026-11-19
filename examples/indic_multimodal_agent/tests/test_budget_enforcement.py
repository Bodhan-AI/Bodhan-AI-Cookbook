"""Tests for user-stated budget parsing and hard cap enforcement in BudgetTracker."""

import pytest
from bodhan_agent.budget import BudgetTracker, parse_stated_budget_inr
from bodhan_mcp.types import ToolError


def test_parse_stated_budget_inr():
    # Paise patterns
    assert parse_stated_budget_inr("Make Marathi subtitles for the voice note, but I only want to spend 5 paise.") == 0.05
    assert parse_stated_budget_inr("spend 50 paise on this task") == 0.50
    assert parse_stated_budget_inr("budget: 10 paise") == 0.10
    assert parse_stated_budget_inr("cap of 25 paise") == 0.25

    # Rupee patterns
    assert parse_stated_budget_inr("Do this under Rs 1") == 1.0
    assert parse_stated_budget_inr("Please complete under Rs. 2.50") == 2.50
    assert parse_stated_budget_inr("spend at most ₹0.75") == 0.75
    assert parse_stated_budget_inr("under 2 rupees") == 2.0
    assert parse_stated_budget_inr("only want to spend 1 rupee") == 1.0

    # No budget mentioned
    assert parse_stated_budget_inr("Transcribe this Hindi voice note and give me English subtitles") is None
    assert parse_stated_budget_inr("Translate this table to Marathi") is None


def test_budget_tracker_enforces_user_stated_cap(tmp_path):
    # User-stated 5 paise (₹0.05) should override CLI --max-cost-inr 5.0
    task_str = "Make Marathi subtitles for the voice note, but I only want to spend 5 paise."
    tracker = BudgetTracker(str(tmp_path), max_steps=10, max_cost_inr=5.0, task=task_str)

    assert tracker.user_stated_budget_inr == 0.05
    assert tracker.max_cost_inr == 0.05
    assert tracker.budget_source == "user_prompt"

    # Preflight check within budget
    assert tracker.check_preflight_budget(0.02) is True

    # Preflight check exceeding 5 paise (even though far below 5.0)
    with pytest.raises(ToolError) as exc_info:
        tracker.check_preflight_budget(0.06)
    assert "BUDGET_EXCEEDED" in str(exc_info.value)
    assert "0.0500" in str(exc_info.value)

    # Runtime cost record exceeding cap
    tracker.record_cost(0.03)  # spent 0.03 <= 0.05
    with pytest.raises(ToolError) as exc_info2:
        tracker.record_cost(0.03)  # spent 0.06 > 0.05
    assert "BUDGET_EXCEEDED" in str(exc_info2.value)


def test_budget_tracker_ignores_higher_user_stated_budget(tmp_path):
    # If user says "under Rs 10", but CLI specifies --max-cost-inr 2.0, the CLI constraint remains effective
    task_str = "Transcribe under Rs 10"
    tracker = BudgetTracker(str(tmp_path), max_steps=10, max_cost_inr=2.0, task=task_str)

    assert tracker.user_stated_budget_inr == 10.0
    assert tracker.max_cost_inr == 2.0
    assert tracker.budget_source == "cli_flag"

