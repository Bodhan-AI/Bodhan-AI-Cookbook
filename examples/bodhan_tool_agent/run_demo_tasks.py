"""Automated runner and self-evaluation script for the Bodhan Task Agent demo suite (Stretch Goal).

Executes all 6 demo tasks, verifies outputs, measures costs and latencies,
calculates pass rate, and generates an evaluation report.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# Enable mock by default for self-evaluation unless explicitly overridden
os.environ.setdefault("BODHAN_MOCK", "1")

# Add src to sys.path
SRC_PATH = Path(__file__).resolve().parent / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from bodhan_agent.agent import TaskAgent

DEMO_ROOT = Path(__file__).resolve().parent
SAMPLE_DIR = DEMO_ROOT / "sample_data"
SAMPLE_WAV = SAMPLE_DIR / "sample_hindi.wav"


def run_demo_suite(out_base: Path) -> dict:
    out_base.mkdir(parents=True, exist_ok=True)
    eval_report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tasks_run": 0,
        "tasks_passed": 0,
        "tasks_failed": 0,
        "pass_rate_percent": 0.0,
        "total_cost_inr": 0.0,
        "total_time_seconds": 0.0,
        "results": [],
    }

    tasks = [
        {
            "id": "task_1",
            "name": "Tamil audio summary of chapter",
            "task_str": "Make a two-minute Tamil audio summary of this chapter",
            "setup": lambda: _get_chapter_file(),
            "expected_status": "completed",
        },
        {
            "id": "task_2",
            "name": "Hindi voice note to English subtitles",
            "task_str": "Transcribe this Hindi voice note and give me English subtitles",
            "setup": lambda: SAMPLE_WAV,
            "expected_status": "completed",
        },
        {
            "id": "task_3",
            "name": "Worksheet translation keeping table",
            "task_str": "Translate this worksheet into Marathi and keep the table",
            "setup": lambda: _get_worksheet_file(),
            "expected_status": "completed",
        },
        {
            "id": "task_4",
            "name": "Filter Telugu clips and translate",
            "task_str": "Which of these clips are in Telugu? Translate those into English",
            "setup": lambda: _get_clips_dir(),
            "expected_status": "completed",
        },
        {
            "id": "task_5",
            "name": "Bengali speech synthesis with female voice",
            "task_str": "Read this paragraph aloud in Bengali with a female voice",
            "setup": lambda: _get_bengali_text_file(),
            "expected_status": "completed",
        },
        {
            "id": "task_6",
            "name": "Impossible task: Video translation into French",
            "task_str": "Translate this video into French",
            "setup": lambda: _get_fake_video(),
            "expected_status": "impossible_task",
        },
    ]

    print("=================================================================")
    print("      BODHAN TASK AGENT - SELF-EVALUATION & BENCHMARK SUITE      ")
    print("=================================================================\n")

    for t in tasks:
        print(f"▶ [{t['id'].upper()}] {t['name']}")
        input_file = t["setup"]()
        task_out = out_base / t["id"]
        t0 = time.time()

        agent = TaskAgent(
            task=t["task_str"],
            files_path=str(input_file),
            out_dir=str(task_out),
        )
        res = agent.run()
        elapsed = round(time.time() - t0, 3)

        # Read report.json
        report_data = {}
        report_file = task_out / "report.json"
        if report_file.exists():
            report_data = json.loads(report_file.read_text(encoding="utf-8"))

        status = res.get("status")
        passed = (status == t["expected_status"])
        cost = report_data.get("total_cost_inr", 0.0)
        steps = report_data.get("total_steps", 0)

        eval_report["tasks_run"] += 1
        if passed:
            eval_report["tasks_passed"] += 1
        else:
            eval_report["tasks_failed"] += 1
        eval_report["total_cost_inr"] = round(eval_report["total_cost_inr"] + cost, 4)
        eval_report["total_time_seconds"] = round(eval_report["total_time_seconds"] + elapsed, 3)

        eval_report["results"].append({
            "task_id": t["id"],
            "name": t["name"],
            "status": status,
            "expected_status": t["expected_status"],
            "passed": passed,
            "steps": steps,
            "cost_inr": cost,
            "latency_seconds": elapsed,
            "artifacts_count": report_data.get("artifacts_count", 0),
        })

        status_label = "PASSED" if passed else "FAILED"
        print(f"  Result: [{status_label}] status='{status}' | steps={steps} | cost=₹{cost:.4f} | time={elapsed}s\n")

    pass_rate = round((eval_report["tasks_passed"] / eval_report["tasks_run"]) * 100.0, 1) if eval_report["tasks_run"] > 0 else 0.0
    eval_report["pass_rate_percent"] = pass_rate

    # Write evaluation summary
    eval_summary_file = out_base / "eval_report.json"
    eval_summary_file.write_text(json.dumps(eval_report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=================================================================")
    print("                     EVALUATION REPORT SUMMARY                   ")
    print("=================================================================")
    print(f"Tasks Run:     {eval_report['tasks_run']}")
    print(f"Tasks Passed:  {eval_report['tasks_passed']} / {eval_report['tasks_run']}")
    print(f"Pass Rate:     {pass_rate}%")
    print(f"Total Cost:    ₹{eval_report['total_cost_inr']:.4f}")
    print(f"Total Time:    {eval_report['total_time_seconds']:.2f}s")
    print(f"Report File:   {eval_summary_file}")
    print("=================================================================\n")

    return eval_report


def _get_chapter_file() -> Path:
    p = SAMPLE_DIR / "chapter_sample.png"
    if not p.exists():
        from PIL import Image
        img = Image.new("RGB", (600, 800), "white")
        img.save(p)
    return p


def _get_worksheet_file() -> Path:
    p = SAMPLE_DIR / "worksheet.png"
    if not p.exists():
        from PIL import Image
        img = Image.new("RGB", (700, 500), "white")
        img.save(p)
    return p


def _get_clips_dir() -> Path:
    d = SAMPLE_DIR / "clips"
    d.mkdir(parents=True, exist_ok=True)
    c1 = d / "clip_01.wav"
    if not c1.exists() and SAMPLE_WAV.exists():
        shutil.copy(SAMPLE_WAV, c1)
    return d


def _get_bengali_text_file() -> Path:
    p = SAMPLE_DIR / "bengali_para.txt"
    p.write_text("বোধন এআই একটি ভারতীয় ভাষার মডেল প্ল্যাটফর্ম।", encoding="utf-8")
    return p


def _get_fake_video() -> Path:
    p = SAMPLE_DIR / "video.mp4"
    if not p.exists():
        p.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    return p


if __name__ == "__main__":
    out_dir = DEMO_ROOT / "outputs" / "demo_runs"
    run_demo_suite(out_dir)
