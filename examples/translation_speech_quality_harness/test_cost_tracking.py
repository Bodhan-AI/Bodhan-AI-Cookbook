import os
from pathlib import Path
from tempfile import TemporaryDirectory

# Configure test-only rates before constructing the client.
os.environ["BODHAN_TRANSLATE_INPUT_INR_PER_1K_TOKENS"] = "1"
os.environ["BODHAN_TRANSLATE_OUTPUT_INR_PER_1K_TOKENS"] = "1"
os.environ["BODHAN_SPEAK_INR_PER_10K_CHARS"] = "6"
os.environ["BODHAN_TRANSCRIBE_INR_PER_MINUTE"] = "0.10"

from bodhan_eval.bodhan_client import BodhanClient, BodhanBudgetExceeded


def main():
    client = BodhanClient(max_cost_inr=0.000001)

    def forbidden_request(*args, **kwargs):
        raise AssertionError("API request should not be attempted")

    client.session.request = forbidden_request

    try:
        client.translate("This is a test request.", "hi")
    except BodhanBudgetExceeded:
        print("PASS: translation blocked before network request")
    else:
        raise AssertionError("Expected BodhanBudgetExceeded")

    # Verify usage accounting and report serialization without API calls.
    client.max_cost_inr = None
    client._record_usage(
        "speak", 0.006, characters=10, language="hi"
    )
    client.usage["characters"]["speak"] += 10
    report = client.get_usage_report()

    assert report["requests"]["speak"] == 1
    assert report["characters"]["speak"] == 10
    assert abs(report["estimated_cost_inr"] - 0.006) < 1e-9
    assert report["budget_enforced"] is False

    print("PASS: usage ledger and cost report")
    print("COST TRACKING TESTS PASSED")


if __name__ == "__main__":
    main()
