"""Tests for pre-flight budget estimation, refusal, and mid-run enforcement (Slice 10)."""

from bodhan_agent.budget import BudgetManager
from bodhan_agent.resolver import FileRef


def test_budget_preflight_estimation_and_refusal():
    """Verify pre-flight refuses overly expensive task without any model calls."""
    # 10-minute audio podcast (600s) + TTS audio version
    large_podcast = FileRef(
        id="A",
        path="/tmp/podcast.wav",
        filename="podcast.wav",
        kind="audio",
        duration_s=600.0,
        pages=None,
        size_mb=25.0,
    )

    budget = BudgetManager(max_cost_inr=0.50)  # Very low budget ceiling of ₹0.50
    estimated = budget.estimate([large_podcast], "Transcribe this 10-minute podcast and generate an audio version")

    # 10 min audio alone = ₹1.00 + TTS + 10% buffer > ₹0.50
    assert estimated > 0.50
    # Must refuse pre-flight
    assert budget.can_afford(estimated) is False


def test_budget_runtime_tracking_and_midrun_clean_stop():
    """Verify runtime charging and mid-run stop when budget is exhausted."""
    budget = BudgetManager(max_cost_inr=1.00)
    assert budget.remaining_inr == 1.00
    assert budget.can_afford(0.40) is True

    # Step 1: spend 0.40
    budget.charge(0.40)
    assert budget.remaining_inr == 0.60
    assert budget.can_afford(0.50) is True

    # Step 2: spend 0.50
    budget.charge(0.50)
    assert budget.remaining_inr == 0.10

    # Step 3: next step costs 0.20 -> cannot afford, clean stop
    assert budget.can_afford(0.20) is False

    # Charge remaining 0.10
    budget.charge(0.10)
    assert budget.remaining_inr == 0.0
    assert budget.can_afford(0.01) is False
