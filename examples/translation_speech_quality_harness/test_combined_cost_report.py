import json
import tempfile
from pathlib import Path

from bodhan_eval.__main__ import create_summary


def make_bodhan_usage(cost=0.01, partial=False):
    return {
        "requests": {
            "translate": 1,
            "speak": 0,
            "transcribe": 0,
        },
        "cache_hits": {
            "translate": 0,
            "speak": 0,
            "transcribe": 0,
        },
        "input_tokens": 100,
        "output_tokens": 50,
        "characters": {"speak": 0},
        "audio_minutes": {"transcribe": 0.0},
        "estimated_cost_inr": cost,
        "cost_is_partial": partial,
    }


def make_llm_usage(cost=0.02, partial=False):
    return {
        "provider": "https://mock-llm.example/v1",
        "model": "mock-model",
        "requests": 1,
        "input_tokens": 200,
        "output_tokens": 100,
        "estimated_cost_inr": cost,
        "cost_is_partial": partial,
    }


def run_test():
    sample_results = [
        {"id": "mock-001", "language": "hi", "status": "passed"},
        {"id": "mock-002", "language": "hi", "status": "warning"},
    ]

    with tempfile.TemporaryDirectory(prefix="indiceval_cost_test_") as temp:
        output_dir = Path(temp)

        # Case 1: Both providers report complete mock pricing.
        summary = create_summary(
            results=sample_results,
            output_dir=output_dir,
            selected_languages=["hi"],
            usage_report=make_bodhan_usage(0.01, False),
            llm_usage_report=make_llm_usage(0.02, False),
        )

        summary_path = output_dir / "summary.json"
        markdown_path = output_dir / "summary.md"

        assert summary_path.exists(), "summary.json was not created"
        assert markdown_path.exists(), "summary.md was not created"

        saved = json.loads(summary_path.read_text(encoding="utf-8"))
        markdown = markdown_path.read_text(encoding="utf-8")

        assert abs(saved["estimated_cost_inr"] - 0.03) < 1e-9, (
            f"Expected combined cost 0.03, got {saved['estimated_cost_inr']}"
        )
        assert saved["cost_tracking_partial"] is False
        assert saved["api_usage"]["estimated_cost_inr"] == 0.01
        assert saved["llm_usage"]["estimated_cost_inr"] == 0.02
        assert "Combined estimated cost subtotal" in markdown
        assert "Estimated LLM cost" in markdown
        assert "₹0.03000000" in markdown

        print("PASS: summary.json contains Bodhan + LLM usage")
        print("PASS: combined estimated cost is ₹0.03")
        print("PASS: complete mock pricing is marked non-partial")
        print("PASS: summary.md contains combined cost details")

        # Case 2: Missing/unknown provider pricing must mark the total partial.
        partial_summary = create_summary(
            results=sample_results,
            output_dir=output_dir,
            selected_languages=["hi"],
            usage_report=make_bodhan_usage(0.01, False),
            llm_usage_report=make_llm_usage(0.0, True),
        )

        assert partial_summary["cost_tracking_partial"] is True
        assert abs(partial_summary["estimated_cost_inr"] - 0.01) < 1e-9

        print("PASS: incomplete LLM pricing marks combined cost as partial")

    print("\nCOMBINED COST REPORT TEST PASSED")
    print("No Bodhan or LLM API requests were made; all usage was mocked.")


if __name__ == "__main__":
    run_test()
