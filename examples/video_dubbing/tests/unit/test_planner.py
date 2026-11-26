from bodhan_dub.agent.cost_tracker import CostAndLatencyTracker
from bodhan_dub.agent.planner import PipelinePlanner
from bodhan_dub.models.segment import DubSegment


def test_planner_schedules_and_condenses_within_retry_budget():
    planner = PipelinePlanner(max_condensation_retries=2)
    seg = DubSegment(id="seg_000", start=0.0, end=5.0, source_text="This is basically a very long sentence.")
    planner.plan_segment_execution([seg])
    assert seg.metadata["plan_status"] == "SCHEDULED"
    assert seg.metadata["condensation_attempts"] == 0

    seg.applied_tempo_factor = 1.15
    assert planner.should_condense(seg) is False

    seg.applied_tempo_factor = 1.25
    assert planner.should_condense(seg) is True

    seg.metadata["condensation_attempts"] = 2
    assert planner.should_condense(seg) is False


def test_cost_tracker_accumulates_calls_and_retries():
    tracker = CostAndLatencyTracker()
    tracker.record_call("translate", 0.5, retries=1)
    tracker.record_call("translate", 1.5, retries=0)
    summary = tracker.get_summary()
    assert summary["translate"].endpoint == "POST /translate"
    assert summary["translate"].call_count == 2
    assert summary["translate"].retry_count == 1
    assert summary["translate"].avg_latency_seconds == 1.0
