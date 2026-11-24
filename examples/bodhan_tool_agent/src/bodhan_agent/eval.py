"""Evaluation harness: runs benchmark suites, measures pass rates, costs, latencies, and reports results."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from bodhan_agent.orchestrator import AgentOrchestrator

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(levelname)s] %(message)s")
logger = logging.getLogger("bodhan_agent.eval")


DEMO_TASKS = [
    {
        "id": "task_1_hindi_transcribe_subtitles",
        "name": "Hindi audio transcription with English subtitles",
        "task": "Transcribe this Hindi audio clip and generate English subtitles with timestamps",
        "sample_dir": "sample_data/hindi_audio",
        "expected_status": "completed",
    },
    {
        "id": "task_2_ocr_worksheet_marathi",
        "name": "English worksheet OCR and Marathi translation",
        "task": "Extract text from this worksheet image and translate it into Marathi",
        "sample_dir": "sample_data/worksheet",
        "expected_status": "completed",
    },
    {
        "id": "task_3_pdf_to_tamil_audio_summary",
        "name": "Scanned multi-page PDF to Tamil audio summary",
        "task": "Make a concise Tamil audio summary of this scanned chapter",
        "sample_dir": "sample_data/chapter_pdf",
        "expected_status": "completed",
    },
    {
        "id": "task_4_telugu_audio_english_translation",
        "name": "Telugu audio identification and English translation",
        "task": "Transcribe these Telugu audio clips and translate them into English",
        "sample_dir": "sample_data/telugu_audio",
        "expected_status": "completed",
    },
    {
        "id": "task_5_bengali_speech_female",
        "name": "Bengali speech synthesis with female voice",
        "task": "Synthesize this Bengali educational text into speech using a female voice",
        "sample_dir": "sample_data/bengali_text",
        "expected_status": "completed",
    },
    {
        "id": "task_6_impossible_french_translation",
        "name": "Impossible request: Translate video into French",
        "task": "Translate this input video into French",
        "sample_dir": "sample_data/sample_video",
        "expected_status": "failed",
        "expected_error_pattern": "unsupported",
    },
]


def run_evaluation(suite: str = "all", out_base: Path | None = None, mock: bool = False) -> dict:
    """Execute evaluation benchmark suite and produce machine-readable metrics."""
    out_dir = out_base or Path("out/evaluation")
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    total_cost = 0.0
    passed_count = 0

    t_suite_start = time.time()

    if mock:
        from unittest.mock import patch
        p_transcribe = patch("bodhan_agent.executor.transcribe_audio")
        p_translate = patch("bodhan_agent.executor.translate_text")
        p_speak = patch("bodhan_agent.executor.synthesize_speech")
        p_ocr = patch("bodhan_agent.executor.extract_text_from_document")

        mock_transcribe = p_transcribe.start()
        mock_transcribe.return_value = {
            "success": True,
            "text": "नमस्ते, यह एक ऑडियो परीक्षण है।",
            "language": "hi",
            "duration_seconds": 5.0,
            "chunks": [
                {"chunk_index": 0, "start_time_s": 0.0, "end_time_s": 5.0, "text": "नमस्ते"}
            ],
            "estimated_cost_inr": 0.05,
        }

        def mock_translate_fn(text, target_language, script="native", **kw):
            if target_language == "fr":
                from bodhan_mcp.errors import UnsupportedLanguageError
                raise UnsupportedLanguageError("fr", "translation", ["hi", "ta", "mr", "en"])
            return {
                "success": True,
                "translation": f"Translated into {target_language}: {text[:30] if text else 'sample text'}",
                "target_language": target_language,
                "script": script,
                "source_char_count": len(text or ""),
                "output_char_count": len(text or ""),
                "estimated_cost_inr": 0.02,
            }

        mock_translate = p_translate.start()
        mock_translate.side_effect = mock_translate_fn

        mock_speak = p_speak.start()
        mock_speak.return_value = {
            "success": True,
            "audio_file_path": str(Path(__file__).resolve().parent.parent / "sample_data/hindi_audio/speech_sample.wav"),
            "duration_seconds": 3.0,
            "language": "hi",
            "voice": "Kavya",
            "estimated_cost_inr": 0.09,
        }

        mock_ocr = p_ocr.start()
        mock_ocr.return_value = {
            "success": True,
            "text": "# Sample Chapter\n\nPlant photosynthesis details.",
            "total_pages": 2,
            "successful_pages": 2,
            "failed_pages": 0,
            "estimated_cost_inr": 0.60,
        }

    try:
        for item in DEMO_TASKS:
            t_id = item["id"]
            logger.info(f"Running evaluation task [{t_id}]: {item['name']}")

            task_out = out_dir / t_id
            orchestrator = AgentOrchestrator(out_dir=task_out, max_steps=20, max_cost_inr=15.0)

            # Check sample files directory
            rel_sample = Path(item["sample_dir"])
            if rel_sample.exists():
                sample_path = rel_sample
            else:
                sample_path = Path(__file__).resolve().parent.parent.parent / item["sample_dir"]
            files_arg = str(sample_path) if sample_path.exists() else None

            t_task_start = time.time()
            try:
                res = orchestrator.run(task_prompt=item["task"], files_path=files_arg)
                duration = time.time() - t_task_start
                cost = res.get("cost_inr", 0.0)
                total_cost += cost

                actual_status = res.get("status", "unknown")
                if item.get("expected_status") == "failed":
                    is_correct = actual_status in {"failed", "partial"}
                else:
                    is_correct = actual_status in {"completed", "partial"}

                if is_correct:
                    passed_count += 1

                results.append({
                    "task_id": t_id,
                    "name": item["name"],
                    "status": actual_status,
                    "expected_status": item["expected_status"],
                    "passed": is_correct,
                    "cost_inr": round(cost, 4),
                    "duration_seconds": round(duration, 2),
                    "artifacts_count": len(res.get("artifacts", [])),
                    "error": res.get("error"),
                })

            except Exception as e:
                duration = time.time() - t_task_start
                if item.get("expected_status") == "failed" and "unsupported" in str(e).lower():
                    passed_count += 1
                    is_correct = True
                else:
                    is_correct = False

                results.append({
                    "task_id": t_id,
                    "name": item["name"],
                    "status": "failed",
                    "expected_status": item["expected_status"],
                    "passed": is_correct,
                    "cost_inr": 0.0,
                    "duration_seconds": round(duration, 2),
                    "artifacts_count": 0,
                    "error": str(e),
                })
    finally:
        if mock:
            p_transcribe.stop()
            p_translate.stop()
            p_speak.stop()
            p_ocr.stop()

    total_duration = time.time() - t_suite_start
    pass_rate = round((passed_count / len(DEMO_TASKS)) * 100.0, 1) if DEMO_TASKS else 0.0

    eval_summary = {
        "suite": suite,
        "total_tasks": len(DEMO_TASKS),
        "passed_tasks": passed_count,
        "pass_rate_percent": pass_rate,
        "total_cost_inr": round(total_cost, 4),
        "total_duration_seconds": round(total_duration, 2),
        "tasks": results,
    }

    report_path = out_dir / "eval_report.json"
    report_path.write_text(json.dumps(eval_summary, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Evaluation complete! Pass rate: {pass_rate}% ({passed_count}/{len(DEMO_TASKS)}) | Report: {report_path}")

    return eval_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bodhan_agent.eval", description="Bodhan Agent Self-Evaluation Harness")
    parser.add_argument("--suite", default="all", help="Evaluation suite name (default: all)")
    parser.add_argument("--out", default="out/evaluation", help="Evaluation report directory")
    parser.add_argument("--mock", action="store_true", help="Run offline using mocked model endpoints for testing without API credit consumption")
    args = parser.parse_args(argv)

    summary = run_evaluation(suite=args.suite, out_base=Path(args.out), mock=args.mock)
    print("\n================ EVALUATION SUMMARY ================")
    print(f"Suite: {summary['suite']}")
    print(f"Tasks Passed: {summary['passed_tasks']}/{summary['total_tasks']} ({summary['pass_rate_percent']}%)")
    print(f"Total Cost: ₹{summary['total_cost_inr']:.4f}")
    print(f"Total Runtime: {summary['total_duration_seconds']:.2f}s")
    print("===================================================\n")
    return 0 if summary["passed_tasks"] == summary["total_tasks"] else 1


if __name__ == "__main__":
    sys.exit(main())
