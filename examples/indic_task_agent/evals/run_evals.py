"""Self-evaluation: run the agent on every task in evals/tasks.json and report a pass rate.

    python evals/run_evals.py                 # all tasks (uses real Bodhan credit and your LLM)
    python evals/run_evals.py --only telugu_clips impossible_french_video

Each run's outputs go to evals/runs/<task id>/; the summary is written to evals/runs/results.json.
A task passes when report.json matches the task's `expect` block (status, artifact kinds/languages,
answer keywords, injection flag, no artifacts for impossible tasks).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

RECIPE = Path(__file__).resolve().parents[1]
RUNS = RECIPE / "evals" / "runs"


def grade(report: dict, expect: dict) -> list[str]:
    problems = []
    if report.get("status") not in expect.get("status", []):
        problems.append(f"status {report.get('status')!r} not in {expect.get('status')}")
    artifacts = report.get("artifacts") or []
    for want in expect.get("artifacts", []):
        if not any(a.get("kind") == want["kind"] and (not want.get("language") or a.get("language") == want["language"])
                   for a in artifacts):
            problems.append(f"missing artifact {want}")
    if expect.get("no_artifacts") and artifacts:
        problems.append(f"expected no artifacts, got {[a['file'] for a in artifacts]}")
    answer = (report.get("answer") or "").lower()
    for word in expect.get("answer_contains", []):
        if word.lower() not in answer:
            problems.append(f"answer does not mention {word!r}")
    if expect.get("injection_flagged") and not report.get("injection_flags"):
        problems.append("prompt injection was not flagged")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", help="task ids to run")
    args = parser.parse_args()
    tasks = json.loads((RECIPE / "evals" / "tasks.json").read_text(encoding="utf-8"))
    if args.only:
        tasks = [t for t in tasks if t["id"] in args.only]
    RUNS.mkdir(parents=True, exist_ok=True)
    results = []
    for t in tasks:
        out = RUNS / t["id"]
        cmd = [sys.executable, "-m", "bodhan_agent", "--task", t["task"], "--files",
               *[str(RECIPE / f) for f in t["files"]], "--out", str(out), *t.get("args", [])]
        started = time.time()
        subprocess.run(cmd, cwd=RECIPE, check=False)
        elapsed = time.time() - started
        report_path = out / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
        problems = grade(report, t["expect"])
        results.append({"id": t["id"], "passed": not problems, "problems": problems, "status": report.get("status"),
                        "cost_inr": (report.get("cost") or {}).get("total_inr"), "elapsed_s": round(elapsed, 1)})
        print(f"{'PASS' if not problems else 'FAIL'}  {t['id']:32} {report.get('status')!s:24} "
              f"Rs {results[-1]['cost_inr'] or 0:.3f}  {elapsed:5.1f}s  {'; '.join(problems)}")
    passed = sum(r["passed"] for r in results)
    summary = {"passed": passed, "total": len(results), "pass_rate": round(passed / max(1, len(results)), 3),
               "total_cost_inr": round(sum(r["cost_inr"] or 0 for r in results), 4), "results": results}
    (RUNS / "results.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\npass rate: {passed}/{len(results)}  total cost Rs {summary['total_cost_inr']:.3f}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
