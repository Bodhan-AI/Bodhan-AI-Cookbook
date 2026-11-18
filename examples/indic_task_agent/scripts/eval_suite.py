#!/usr/bin/env python3
"""Run the task suite through the real CLI and report a pass rate.

Live mode spends money: every task runs `python -m bodhan_agent` against the
Bodhan API. Replay mode re-checks runs recorded under a folder (one subfolder
per task id) without calling anything.

    python scripts/eval_suite.py                  # live, skips tasks whose media is absent
    python scripts/eval_suite.py --only hindi_note_srt --max-cost-inr 1.0
    python scripts/eval_suite.py --replay demo_runs
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "demo_tasks"


def media_root() -> Path:
    return Path(os.environ.get("BODHAN_DEMO_MEDIA") or ROOT / "demo_tasks")


def specs(only: str | None) -> list[dict]:
    found = []
    for path in sorted(TASKS.glob("*.json")):
        if path.name == "media.manifest.json":
            continue
        spec = json.loads(path.read_text(encoding="utf-8"))
        if only and spec["id"] not in only.split(","):
            continue
        found.append(spec)
    return found


def input_paths(spec: dict) -> list[Path]:
    return [media_root() / rel for rel in spec["files"]]


def check(run_dir: Path, spec: dict) -> list[str]:
    """The expectations in the task file, against the four run files."""
    report_path = run_dir / "report.json"
    if not report_path.is_file():
        return [f"no report.json in {run_dir}"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    expect = spec["expect"]
    failures: list[str] = []
    if report["status"] not in expect.get("status", ["completed"]):
        failures.append(f"status {report['status']}")
    artifacts = [p for p in (run_dir / "artifacts").iterdir() if p.is_file()] if (run_dir / "artifacts").is_dir() else []
    for pattern in expect.get("artifacts", []):
        if not any(p.match(pattern) for p in artifacts):
            failures.append(f"no artifact matching {pattern}")
    if expect.get("no_artifacts") and artifacts:
        failures.append(f"{len(artifacts)} artifact(s) written for a task that must produce none")
    passed = {c["check"] for c in report.get("verification", []) if c["result"] == "passed"}
    for name in expect.get("checks", []):
        if name not in passed:
            failures.append(f"check {name} did not pass")
    for code in expect.get("errors", []):
        if not any(e.get("code") == code for e in report.get("errors", [])):
            failures.append(f"no {code} error")
    ceiling = float(expect.get("max_cost_inr", report["budget"]["max_cost_inr"]))
    if report["budget"]["cost_inr"] > ceiling + 1e-9:
        failures.append(f"spent Rs {report['budget']['cost_inr']:.2f} > Rs {ceiling:.2f}")
    needle = expect.get("trace_contains")
    if needle:
        trace = (run_dir / "trace.jsonl").read_text(encoding="utf-8") if (run_dir / "trace.jsonl").is_file() else ""
        if needle not in trace:
            failures.append(f"trace does not mention {needle}")
    return failures


def run_live(spec: dict, out: Path, max_cost: float | None) -> str | None:
    """None when the task ran, otherwise the reason it was skipped."""
    paths = input_paths(spec)
    if spec.get("needs_media", True) and not all(p.exists() for p in paths):
        missing = [str(p.relative_to(media_root())) for p in paths if not p.exists()]
        return "media missing: " + ", ".join(missing)
    files = ",".join(str(p) for p in paths)
    args = [sys.executable, "-m", "bodhan_agent", "--task", spec["task"], "--files", files, "--out", str(out), *spec.get("args", [])]
    if max_cost is not None:
        if "--max-cost-inr" in args:
            args[args.index("--max-cost-inr") + 1] = str(max_cost)
        else:
            args += ["--max-cost-inr", str(max_cost)]
    subprocess.run(args, cwd=ROOT, check=False, timeout=1800)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", help="comma-separated task ids")
    parser.add_argument("--replay", type=Path, help="folder of recorded runs, one subfolder per task id")
    parser.add_argument("--out", type=Path, default=ROOT / "out" / "eval", help="where live runs are written")
    parser.add_argument("--max-cost-inr", type=float, help="override every task's budget (live only)")
    parser.add_argument("--strict", action="store_true", help="a skipped task counts as a failure")
    args = parser.parse_args(argv)

    rows: list[tuple[str, str, str]] = []
    for spec in specs(args.only):
        run_dir = args.replay / spec["id"] if args.replay else args.out / spec["id"]
        if args.replay:
            if not run_dir.is_dir():
                rows.append((spec["id"], "SKIP", f"no recorded run at {run_dir}"))
                continue
            skip = None
        else:
            out = args.out / spec["id"]
            if out.exists():
                import shutil

                shutil.rmtree(out)
            skip = run_live(spec, out, args.max_cost_inr)
        if skip:
            rows.append((spec["id"], "SKIP", skip))
            continue
        failures = check(run_dir, spec)
        rows.append((spec["id"], "PASS" if not failures else "FAIL", "; ".join(failures) or report_line(run_dir)))

    width = max((len(r[0]) for r in rows), default=8)
    for task_id, verdict, detail in rows:
        print(f"{task_id:<{width}}  {verdict:<4}  {detail}")
    passed = sum(1 for r in rows if r[1] == "PASS")
    failed = sum(1 for r in rows if r[1] == "FAIL")
    skipped = sum(1 for r in rows if r[1] == "SKIP")
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped of {len(rows)}")
    if args.strict:
        return 1 if failed or skipped else 0
    return 1 if failed else 0


def report_line(run_dir: Path) -> str:
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    return f"Rs {report['budget']['cost_inr']:.2f}, {report['duration_s']:.0f}s"


if __name__ == "__main__":
    raise SystemExit(main())
