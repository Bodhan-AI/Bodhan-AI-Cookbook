
from bodhan_agent.cost import CostTracker


def main():
    print("Testing OmniAgent AI Cost Tracking...\n")

    # Hypothetical test prices, not actual Bodhan prices.
    tracker = CostTracker({
        "extract_text": "0.50",
        "translate_text": "0.25",
    })

    tracker.record_call("extract_text")
    tracker.record_call("translate_text")

    report = tracker.report()
    print("Known-price test:", report)

    assert report["total_calls"] == 2
    assert report["total_cost_inr"] == "0.75"
    assert report["cost_complete"] is True

    print("PASS: Known costs calculated correctly!")

    # Test missing pricing information.
    unknown_tracker = CostTracker()

    unknown_tracker.record_call("extract_text")
    unknown_tracker.record_call("translate_text")

    unknown_report = unknown_tracker.report()
    print("\nUnknown-price test:", unknown_report)

    assert unknown_report["total_calls"] == 2
    assert unknown_report["unknown_cost_calls"] == 2
    assert unknown_report["total_cost_inr"] is None
    assert unknown_report["cost_complete"] is False

    print("PASS: Unknown costs are not misreported!")
    print("PASS: Cost tracking works correctly!")


if __name__ == "__main__":
    main()
