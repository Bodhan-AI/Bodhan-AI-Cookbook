"""Run the demo task suite through the real CLI and report a pass rate.

    python demo/run_demo.py                  # all tasks
    python demo/run_demo.py telugu_clips     # one task
    python demo/run_demo.py --cold           # empty model cache, so costs and times are real

Each task runs as `python -m bodhan_agent ...` into demo/runs/<id>/. Checks are deliberately simple and
independent of the agent: expected status, expected output file types, and a few answer facts.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check(task: dict, out: Path) -> tuple[bool, list[str]]:
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    expect = task["expect"]
    problems = []
    if report["status"] != expect["status"]:
        problems.append(f"status {report['status']}, expected {expect['status']}")
    artifacts = report["artifacts"]
    for wanted in expect.get("outputs", []):
        if not any(a.endswith(tuple(wanted.split("|"))) for a in artifacts):
            problems.append(f"no {wanted} output")
    if expect.get("outputs") == [] and artifacts:
        problems.append("outputs were listed for a task that should produce none")
    if expect["status"] == "completed" and not report["all_checks_passed"]:
        problems.append("an output failed its check")
    answer = (out / "result.md").read_text(encoding="utf-8").lower()
    for word in expect.get("answer_mentions", []):
        if word.lower() not in answer:
            problems.append(f"answer does not mention {word}")
    if "table_in" in expect:
        md = [a for a in artifacts if a.endswith(expect["table_in"])]
        if not md or "|" not in (out / md[0]).read_text(encoding="utf-8"):
            problems.append("no table in the Markdown output")
    return not problems, problems


def main(argv: list[str]) -> int:
    cold = "--cold" in argv
    selected = [a for a in argv if not a.startswith("--")]
    cache_args = []
    if cold:
        cache = ROOT / "demo" / "runs" / f".cache-cold-{int(time.time())}"
        cache_args = ["--cache-dir", str(cache)]
        print(f"Cold run: empty model cache at {cache.relative_to(ROOT)}")
    tasks = json.loads((ROOT / "demo" / "tasks.json").read_text(encoding="utf-8"))
    if selected:
        tasks = [t for t in tasks if t["id"] in selected]
    rows = []
    for task in tasks:
        out = ROOT / "demo" / "runs" / task["id"]
        started = time.monotonic()
        proc = subprocess.run(
            [sys.executable, "-m", "bodhan_agent", "--task", task["task"], "--files", *task["files"], "--out", str(out), *cache_args],
            cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True,
        )
        elapsed = round(time.monotonic() - started, 1)
        if not (out / "report.json").exists():
            rows.append((task["id"], False, ["no report written: " + proc.stderr.strip()[-200:]], 0.0, elapsed, 0))
            continue
        ok, problems = check(task, out)
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        rows.append((task["id"], ok, problems, report["cost_inr"], elapsed, report["steps_used"]))
        print(f"{'PASS' if ok else 'FAIL'}  {task['id']}  ({report['status']}, {report['steps_used']} steps, INR {report['cost_inr']:.4f}, {elapsed}s)"
              + (f"  -> {'; '.join(problems)}" if problems else ""))
    passed = sum(1 for r in rows if r[1])
    mode = "cold cache: every model call paid for" if cold else "shared cache: repeated inputs are free"
    lines = ["# Demo results", "", f"Passed {passed} of {len(rows)} ({mode}).", "", "| Task | Result | Steps | Cost (INR) | Time (s) | Notes |", "|---|---|---|---|---|---|"]
    for task_id, ok, problems, cost, elapsed, steps in rows:
        lines.append(f"| [{task_id}](runs/{task_id}/result.md) | {'pass' if ok else 'fail'} | {steps} | {cost:.4f} | {elapsed} | {'; '.join(problems)} |")
    (ROOT / "demo" / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nPassed {passed} of {len(rows)}. Summary: demo/results.md")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
