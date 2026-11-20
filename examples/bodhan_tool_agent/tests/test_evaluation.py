from pathlib import Path

from bodhan_agent.evaluation import run_evaluation


def test_hidden_task_simulation_suite_has_ten_or_more_passing_scenarios() -> None:
    scenarios = Path(__file__).parents[1] / "evaluation" / "scenarios.json"

    report = run_evaluation(scenarios)

    assert report["external_api_calls"] == 0
    assert report["scenario_count"] >= 10
    assert report["failed"] == 0
    assert report["pass_rate"] == 1.0
