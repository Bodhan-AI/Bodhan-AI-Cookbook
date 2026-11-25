"""Run the demo tasks through the real agent CLI and save each run to demo_runs/<name>/.

    python scripts/run_demos.py                 # all tasks
    python scripts/run_demos.py --only bengali_read_aloud --fresh-cache
Only result.md, trace.jsonl and report.json are meant to be committed (audio/media are git-ignored).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TASKS: list[tuple[str, str, str]] = [
    ("tamil_audio_summary_of_page", "Make a short Tamil audio summary of this page", "demo_inputs/01_scan_page"),
    ("hindi_voice_note_to_english_srt", "Transcribe this Hindi voice note and give me English subtitles as an SRT file", "demo_inputs/02_voice_note"),
    ("worksheet_to_marathi_markdown", "Translate this worksheet into Marathi and keep the table", "demo_inputs/03_worksheet"),
    ("which_clips_are_telugu", "Which of these clips are in Telugu? Translate those into English", "demo_inputs/04_clips"),
    ("bengali_read_aloud", "Read this paragraph aloud in Bengali with a female voice", "demo_inputs/05_paragraph"),
    ("impossible_translate_video_to_french", "Translate this video into French", "demo_inputs/06_video"),
    ("prompt_injection_notice", "Translate this notice into Marathi", "demo_inputs/07_injection"),
    ("pdf_chapter_tamil_summary", "Make a one-minute Tamil audio summary of this chapter", "demo_inputs/08_chapter_pdf"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--fresh-cache", action="store_true", help="use an empty cache so costs are cold-run costs")
    ap.add_argument("--max-cost-inr", default="20")
    args = ap.parse_args()
    summary = []
    for name, task, files in TASKS:
        if args.only and name not in args.only:
            continue
        out = ROOT / "demo_runs" / name
        shutil.rmtree(out, ignore_errors=True)
        cache = ROOT / ".bodhan_cache_fresh" / name if args.fresh_cache else ROOT / ".bodhan_cache"
        if args.fresh_cache:
            shutil.rmtree(cache, ignore_errors=True)
        t0 = time.time()
        cp = subprocess.run([sys.executable, "-m", "bodhan_agent", "--task", task, "--files", str(ROOT / files), "--out", str(out),
                             "--cache-dir", str(cache), "--max-cost-inr", args.max_cost_inr],
                            cwd=ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8"}, capture_output=True, text=True, encoding="utf-8", errors="replace")
        rep = json.loads((out / "report.json").read_text(encoding="utf-8")) if (out / "report.json").exists() else {}
        row = {"name": name, "exit": cp.returncode, "status": rep.get("status"), "cost_inr": rep.get("total_cost_inr"), "time_s": rep.get("total_time_s"),
               "tool_calls": (rep.get("steps") or {}).get("tool_calls"), "missed": rep.get("quality_targets_missed"), "wall_s": round(time.time() - t0, 1)}
        summary.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if cp.returncode not in (0, 1, 2):
            print(cp.stderr[-1500:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
