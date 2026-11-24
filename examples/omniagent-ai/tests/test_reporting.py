
import json
import tempfile
from pathlib import Path

from bodhan_agent.reporting import ReportGenerator


def main():
    print("Testing OmniAgent AI Reporting...\n")

    task = (
        "Extract Hindi text from an image "
        "and translate it into English."
    )

    sample_result = {
        "status": "success",
        "history": [
            {
                "tool": "extract_text",
                "arguments": {
                    "file_path": "data/sample_ocr.png",
                    "language": "hi",
                },
                "result": {
                    "status": "success",
                    "extracted_text": "नमस्ते भारत",
                },
            },
            {
                "tool": "translate_text",
                "arguments": {
                    "text": "नमस्ते भारत",
                    "source_lang": "hi",
                    "target_lang": "en",
                },
                "result": {
                    "status": "success",
                    "translated_text": "hello india",
                },
            },
        ],
        "budget": {
            "max_tool_calls": 2,
            "tool_calls_used": 2,
            "remaining": 0,
        },
        "cost": {
            "currency": "INR",
            "total_calls": 2,
            "total_cost_inr": None,
            "cost_complete": False,
        },
        "trace_path": "out/trace.jsonl",
    }

    with tempfile.TemporaryDirectory() as temp_dir:
        generator = ReportGenerator(
            output_dir=temp_dir
        )

        paths = generator.generate(
            task=task,
            run_result=sample_result,
        )

        report_path = Path(
            paths["report_path"]
        )

        result_path = Path(
            paths["result_path"]
        )

        assert report_path.exists()
        assert result_path.exists()

        report = json.loads(
            report_path.read_text(
                encoding="utf-8"
            )
        )

        markdown = result_path.read_text(
            encoding="utf-8"
        )

        assert report["status"] == "success"
        assert report["completed_steps"] == 2

        assert report["executed_tools"] == [
            "extract_text",
            "translate_text",
        ]

        assert (
            report["cost"]["total_cost_inr"]
            is None
        )

        assert "नमस्ते भारत" in markdown
        assert "hello india" in markdown
        assert "Actual API cost is unknown" in markdown

        print("PASS: report.json generated!")
        print("PASS: result.md generated!")
        print("PASS: Hindi Unicode preserved!")
        print("PASS: Workflow results verified!")
        print("PASS: Unknown API costs handled!")

    print("\nPASS: Reporting module works correctly!")


if __name__ == "__main__":
    main()
