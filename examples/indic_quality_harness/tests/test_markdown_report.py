
from bodhan_eval.report import build_markdown_report


def test_markdown_report_contains_summary():
    report = {
        "total_items": 4,
        "total_checks": 8,
        "passed_checks": 7,
        "failed_checks": 1,
        "review_checks": 0,
        "languages": {"hi": 2, "ta": 1, "bn": 1},
        "errors_by_category": {"number_mismatch": 1},
        "items": [
            {
                "id": "hi-002",
                "language": "hi",
                "checks": [
                    {
                        "category": "number_mismatch",
                        "passed": False,
                        "severity": "high",
                        "evidence": {"source_numbers": ["500"]},
                    }
                ],
            }
        ],
    }

    markdown = build_markdown_report(report)

    assert "# Indic Translation" in markdown
    assert "Total items: **4**" in markdown
    assert "number_mismatch" in markdown
    assert "hi-002" in markdown


def test_markdown_report_includes_calibration():
    report = {
        "total_items": 0,
        "total_checks": 0,
        "passed_checks": 0,
        "failed_checks": 0,
        "review_checks": 0,
        "languages": {},
        "errors_by_category": {},
        "items": [],
    }

    calibration = {
        "metrics": {
            "overall": {
                "precision": 1.0,
                "recall": 1.0,
                "f1": 1.0,
                "tp": 4,
                "fp": 0,
                "fn": 0,
            },
            "categories": {},
        }
    }

    markdown = build_markdown_report(report, calibration)

    assert "## Calibration" in markdown
    assert "100.00%" in markdown
