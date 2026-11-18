"""Self-evaluation runner for bodhan_agent benchmarking across test suites."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml

import io
import math
import struct
import wave

from bodhan_agent import loop
from bodhan_agent.planner import TaskInterpretation
from bodhan_agent.state import TaskStatus


def make_wav(duration_s: float, sample_rate: int = 16000) -> bytes:
    frames = int(max(0.0, duration_s) * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        for i in range(frames):
            v = int(32767 * math.sin(2 * math.pi * 440 * i / sample_rate))
            w.writeframes(struct.pack("<h", v))
    return buf.getvalue()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("bodhan.eval")


MINIMAL_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05"
    b"\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

MINIMAL_PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 144]/Contents 4 0 R>>endobj\n"
    b"4 0 obj<</Length 15>>stream\n"
    b"BT /F1 12 Tf ET\n"
    b"endstream\nendobj\n"
    b"xref\n0 5\n0000000000 65535 f \n0000000009 00000 n \n0000000056 00000 n \n0000000111 00000 n \n0000000212 00000 n \n"
    b"trailer<</Size 5/Root 1 0 R>>\nstartxref\n276\n%%EOF\n"
)


def load_suite(yaml_path: str) -> dict:
    with open(yaml_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


async def evaluate_task_mocked(task_def: dict, eval_dir: Path) -> dict:
    """Run an individual task with mock clients to verify control flow and expected status."""
    task_id = task_def["id"]
    task_text = task_def["task"]
    expected_status = task_def.get("expected_status", "completed")

    task_in_dir = eval_dir / "inputs" / task_id
    task_in_dir.mkdir(parents=True, exist_ok=True)
    task_out_dir = eval_dir / "runs" / task_id
    task_out_dir.mkdir(parents=True, exist_ok=True)

    # Populate dummy files if specified
    files_to_create = task_def.get("files", [])
    if files_to_create and "nonexistent" not in files_to_create[0]:
        for fname in files_to_create:
            p = task_in_dir / fname
            if fname.endswith(".wav"):
                p.write_bytes(make_wav(2.0))
            elif fname.endswith(".pdf"):
                p.write_bytes(MINIMAL_PDF_BYTES)
            elif fname.endswith((".png", ".jpg")):
                p.write_bytes(MINIMAL_PNG_BYTES)
            else:
                p.write_text("नमस्ते यह एक परीक्षण दस्तावेज़ है।", encoding="utf-8")

    # If impossible task (e.g. French, Japanese)
    t0 = time.time()
    try:
        # Mock external APIs to ensure no real money is spent during evaluation
        mock_stt = AsyncMock()
        mock_stt.transcribe_chunk.return_value = {"text": "नमस्ते प्रतिलेखन", "language": "hi"}
        mock_trans = AsyncMock()
        mock_trans.translate_text.return_value = {"translated_text": "Translated content", "output_tokens": 5}
        mock_speak = AsyncMock()
        mock_speak.synthesize_chunk.return_value = make_wav(1.0)
        mock_ocr = AsyncMock()
        mock_ocr.ocr_image_file.return_value = "ओसीआर पाठ"
        mock_ocr.ocr_image_bytes.return_value = "ओसीआर पाठ"

        with patch("bodhan_agent.planner._call_llm_json", side_effect=RuntimeError("Offline evaluation mode - using deterministic fallback planner")), \
             patch("bodhan_mcp.tools.stt.TranscribeClient", return_value=mock_stt), \
             patch("bodhan_mcp.tools.translate.TranslateClient", return_value=mock_trans), \
             patch("bodhan_mcp.tools.tts.SpeakClient", return_value=mock_speak), \
             patch("bodhan_mcp.tools.ocr.OCRClient", return_value=mock_ocr):

            state = await loop.run(
                task=task_text,
                files_dir=str(task_in_dir if files_to_create and "nonexistent" not in files_to_create[0] else eval_dir / "missing"),
                out_dir=str(task_out_dir),
                max_steps=task_def.get("max_steps", 30),
                max_cost_inr=task_def.get("max_cost_inr", 20.0),
            )

        actual_status = state.status.value if hasattr(state.status, "value") else str(state.status)
        passed = (actual_status == expected_status)

        return {
            "id": task_id,
            "name": task_def.get("name", task_id),
            "expected_status": expected_status,
            "actual_status": actual_status,
            "passed": passed,
            "spent_inr": state.spent_inr,
            "steps_used": state.steps_used,
            "latency_seconds": round(time.time() - t0, 3),
            "stop_reason": state.stop_reason,
        }

    except Exception as exc:
        logger.exception("Evaluation failed on task %s", task_id)
        return {
            "id": task_id,
            "name": task_def.get("name", task_id),
            "expected_status": expected_status,
            "actual_status": "error",
            "passed": False,
            "spent_inr": 0.0,
            "steps_used": 0,
            "latency_seconds": round(time.time() - t0, 3),
            "error": str(exc),
        }


def run_evaluation(suite_path: str, out_base_dir: str) -> dict:
    """Run all evaluation cases and produce a pass-rate report."""
    eval_dir = Path(out_base_dir)
    eval_dir.mkdir(parents=True, exist_ok=True)

    suite = load_suite(suite_path)
    tasks = suite.get("tasks", [])

    print(f"\n=======================================================")
    print(f"  Bodhan Agent Evaluation Suite: {len(tasks)} Tasks")
    print(f"=======================================================\n")

    results = []
    for t in tasks:
        res = asyncio.run(evaluate_task_mocked(t, eval_dir))
        status_mark = "PASS" if res["passed"] else "FAIL"
        print(f"[{status_mark}] {res['id']:<35} expected={res['expected_status']:<12} actual={res['actual_status']:<12}")
        results.append(res)

    passed_count = sum(1 for r in results if r["passed"])
    pass_rate = round((passed_count / len(tasks)) * 100, 1) if tasks else 0.0

    report = {
        "suite_version": suite.get("version", "1.0"),
        "total_tasks": len(tasks),
        "passed_tasks": passed_count,
        "failed_tasks": len(tasks) - passed_count,
        "pass_rate_pct": pass_rate,
        "tasks": results,
    }

    report_path = eval_dir / "eval_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n=======================================================")
    print(f"  Summary: {passed_count}/{len(tasks)} passed ({pass_rate}%)")
    print(f"  Report: {report_path.resolve()}")
    print(f"=======================================================\n")

    return report


def main():
    parser = argparse.ArgumentParser(description="Run Bodhan evaluation suite.")
    parser.add_argument("--suite", default="tests/eval/suite.yaml", help="Path to suite.yaml")
    parser.add_argument("--out", default="./out/eval", help="Directory for eval reports")
    args = parser.parse_args()

    run_evaluation(args.suite, args.out)


if __name__ == "__main__":
    main()
