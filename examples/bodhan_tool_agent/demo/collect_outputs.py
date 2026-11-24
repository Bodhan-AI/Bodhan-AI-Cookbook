"""Copy the demo task runs into docs/demo_outputs/ so the README can link outputs and traces.

    python demo/collect_outputs.py

For each task in demo/tasks.json it takes the most recent run under demo_runs/ and copies
result.md, report.json, trace.jsonl and the text artifacts (subtitles, Markdown, text, JSON).
Audio, images and video are not copied: media stays out of git.

It refuses to copy anything that contains one of the key values from your .env file, and it
prints a per-task table (status, steps, cost, time) for the PR's "Cost and runtime" section.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "demo_runs"
DEST = ROOT / "docs" / "demo_outputs"
TEXT_ARTIFACTS = {".srt", ".md", ".txt", ".json", ".vtt"}
SECRET_NAMES = ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY", "LLM_API_KEY", "LLM_FALLBACK_API_KEY", "LLM_FALLBACK2_API_KEY")


def secrets() -> list[str]:
    env = ROOT / ".env"
    values = []
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            if name.strip() in SECRET_NAMES and len(value) >= 8:
                values.append(value)
    return values


def latest_run(task_id: str) -> Path | None:
    runs = [p.parent for p in RUNS.glob(f"**/{task_id}/report.json")]
    return max(runs, key=lambda p: (p / "report.json").stat().st_mtime) if runs else None


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    tasks = json.loads((ROOT / "demo" / "tasks.json").read_text(encoding="utf-8"))
    keys = secrets()
    if DEST.exists():
        shutil.rmtree(DEST)
    rows, missing = [], []
    for task in tasks:
        run = latest_run(task["id"])
        if run is None:
            missing.append(task["id"])
            continue
        files = [run / name for name in ("result.md", "report.json", "trace.jsonl") if (run / name).exists()]
        artifacts = run / "artifacts"
        if artifacts.is_dir():
            files += [f for f in sorted(artifacts.rglob("*")) if f.is_file() and f.suffix.lower() in TEXT_ARTIFACTS]
        for f in files:
            text = f.read_text(encoding="utf-8", errors="replace")
            if any(k in text for k in keys):
                print(f"STOPPED: {f} contains one of your keys. Nothing was copied for {task['id']}.", file=sys.stderr)
                return 1
        target = DEST / task["id"]
        for f in files:
            out = target / f.relative_to(run)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, out)
        report = json.loads((run / "report.json").read_text(encoding="utf-8"))
        cost = (report.get("cost") or {}).get("spent_inr", 0.0)
        rows.append((task["id"], report.get("status"), report.get("steps_used"), cost, report.get("duration_s")))

    print(f"Copied {len(rows)} task runs to docs/demo_outputs/ (text only, no media).\n")
    print("| Task | Status | Steps | Bodhan cost | Time |")
    print("| --- | --- | --- | --- | --- |")
    for task_id, status, steps, cost, seconds in rows:
        print(f"| {task_id} | {status} | {steps} | ₹{cost:.3f} | {seconds:.0f} s |" if seconds is not None else f"| {task_id} | {status} | {steps} | ₹{cost:.3f} | |")
    if missing:
        print("\nNo run found for: " + ", ".join(missing), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
