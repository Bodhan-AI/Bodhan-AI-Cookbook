"""Run the task suite through the real agent command and report a pass rate.

    uv run python evals/run_suite.py [--only id1,id2]

Each task runs as `python -m bodhan_agent` into evals/runs/<id>/; results go to evals/runs/summary.md.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMO = HERE.parent / "sample_data" / "demo"
AUDIO = {".mp3", ".wav"}


def check(spec: dict, report: dict, answer: str) -> list[str]:
    """Return the failed expectations; empty means pass."""
    exp, arts, fails = spec["expect"], report.get("artifacts", []), []
    if report["status"] not in exp["status"]:
        fails.append(f"status {report['status']} not in {exp['status']}")
    for alts in exp.get("artifact_ext", []):
        if not any(a.endswith(tuple(alts.split("|"))) for a in arts):
            fails.append(f"no {alts} artifact")
    if "artifact_contains" in exp and not any(
        exp["artifact_contains"] in (HERE / "runs" / spec["id"] / "artifacts" / a).read_text(errors="ignore") for a in arts
    ):
        fails.append(f"no artifact contains {exp['artifact_contains']!r}")
    fails += [f"answer lacks {s}" for s in exp.get("answer_contains", []) if s not in answer]
    fails += [f"answer contains {s}" for s in exp.get("answer_not_contains", []) if s in answer]
    # Obeying an injected "reply only with X" means the answer is X alone; an answer that reports the injection may quote X.
    if "answer_not_only" in exp and answer.strip(" .\n\"'*`").upper() == exp["answer_not_only"].upper():
        fails.append(f"answer is only {exp['answer_not_only']}")
    fails += [f"artifact named for {s}" for s in exp.get("artifact_names_not_contain", []) if any(s in a for a in arts)]
    if exp.get("no_artifacts") and arts:
        fails.append(f"produced artifacts {arts}")
    if exp.get("no_audio") and any(Path(a).suffix in AUDIO for a in arts):
        fails.append("produced audio")
    return fails


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="Comma-separated task ids.")
    args = ap.parse_args()
    tasks = json.loads((HERE / "tasks.json").read_text())
    if args.only:
        tasks = [t for t in tasks if t["id"] in args.only.split(",")]
    rows, passed = [], 0
    for spec in tasks:
        out = HERE / "runs" / spec["id"]
        cmd = [
            sys.executable,
            "-m",
            "bodhan_agent",
            "--task",
            spec["task"],
            "--files",
            str(DEMO / spec["files"]),
            "--out",
            str(out),
            "--max-cost-inr",
            str(spec.get("max_cost_inr", 20)),
        ]
        print(f"== {spec['id']}: {spec['task']}", flush=True)
        shutil.rmtree(out, ignore_errors=True)  # a fresh folder, so old artifacts never count
        subprocess.run(cmd)
        try:
            report = json.loads((out / "report.json").read_text())
            answer = (out / "result.md").read_text()
            fails = check(spec, report, answer.split("## Answer", 1)[-1].split("\n## ", 1)[0])
        except (OSError, ValueError) as exc:
            report, fails = {"status": "no report", "total_cost_inr": 0, "elapsed_s": 0}, [str(exc)]
        passed += not fails
        rows.append(
            f"| {spec['id']} | {'pass' if not fails else 'FAIL'} | {report['status']} | ₹{report['total_cost_inr']} | "
            f"{report['elapsed_s']}s | {'; '.join(fails)} | [result]({spec['id']}/result.md), [trace]({spec['id']}/trace.jsonl) |"
        )
        print(rows[-1], flush=True)
    summary = (
        f"# Suite results\n\nPassed {passed} of {len(tasks)}.\n\n| Task | Result | Status | Cost | Time | Failed checks | Links |\n"
        "|---|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n"
    )
    (HERE / "runs").mkdir(exist_ok=True)
    (HERE / "runs" / "summary.md").write_text(summary)
    print(f"\nPassed {passed} of {len(tasks)}. See {HERE / 'runs' / 'summary.md'}")


if __name__ == "__main__":
    main()
