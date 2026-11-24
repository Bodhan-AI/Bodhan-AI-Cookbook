"""Run the suite: each case is one real `python -m bodhan_agent` subprocess, graded by code afterwards."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .checks import grade

RECIPE = Path(__file__).resolve().parents[2]
DEFAULT_SUITE = RECIPE / "evals" / "suite.json"


@dataclass
class Case:
    id: str
    task: str
    files: list[str]
    checks: list[dict]
    tags: list[str] = field(default_factory=list)
    max_cost_inr: float = 1.0
    est_cost_inr: float = 0.0
    max_steps: int = 30
    max_minutes: float = 15.0
    inputs_must_exist: bool = True

    def paths(self, root: Path) -> list[Path]:
        return [(root / f) if not Path(f).is_absolute() else Path(f) for f in self.files]


def load_suite(path: Path) -> list[Case]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = [Case(**c) for c in data["cases"]]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise SystemExit(f"duplicate case ids in {path}")
    return cases


def select(cases: list[Case], only: list[str] | None, tags: list[str] | None, include_expensive: bool) -> list[Case]:
    if only:
        unknown = set(only) - {c.id for c in cases}
        if unknown:
            raise SystemExit(f"unknown case id(s): {sorted(unknown)}")
        return [c for c in cases if c.id in only]  # naming a case runs it even if it is expensive
    picked = [c for c in cases if include_expensive or "expensive" not in c.tags]
    if tags:
        picked = [c for c in picked if set(tags) & set(c.tags)]
    return picked


def run_case(case: Case, out: Path, root: Path, max_cost_inr: float) -> dict:
    """Run the agent CLI for one case. Returns run metadata (exit code, wall time, stderr tail)."""
    cmd = [sys.executable, "-m", "bodhan_agent", "--task", case.task, "--files", *map(str, case.paths(root)),
           "--out", str(out), "--max-steps", str(case.max_steps), "--max-cost-inr", f"{max_cost_inr:.4f}",
           "--max-minutes", str(case.max_minutes)]
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, env=os.environ.copy(),
                              timeout=case.max_minutes * 60 + 120)
        code, tail = proc.returncode, (proc.stderr or "")[-1500:]
    except subprocess.TimeoutExpired:
        code, tail = None, "harness timeout: the agent did not stop within its own time limit"
    return {"exit_code": code, "wall_s": round(time.monotonic() - started, 1), "stderr_tail": tail}


def score_case(case: Case, out: Path, meta: dict) -> dict:
    checks = grade(out, case.checks)
    report = {}
    if (out / "report.json").exists():
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    crashed = meta.get("exit_code") not in (0, 1, "regraded")
    return {
        "id": case.id,
        "task": case.task,
        "tags": case.tags,
        "passed": not crashed and all(c["passed"] for c in checks),
        "checks": checks,
        "status": report.get("status"),
        "steps": report.get("steps"),
        "cost_inr": float(report.get("total_cost_inr") or 0),
        "agent_time_s": report.get("total_time_s"),
        "llm": report.get("llm"),
        "out": str(out),
        **meta,
    }


def summarize(results: list[dict], skipped: list[dict], llm_model: str, repeat: int) -> dict:
    ran = results
    by_case: dict[str, list[dict]] = {}
    for r in ran:
        by_case.setdefault(r["id"], []).append(r)
    checks = [c for r in ran for c in r["checks"]]
    return {
        "llm_model": llm_model,
        "repeat": repeat,
        "runs": len(ran),
        "runs_passed": sum(r["passed"] for r in ran),
        "pass_rate": round(sum(r["passed"] for r in ran) / len(ran), 3) if ran else None,
        # A case is consistent only if every repeat passed (pass^k).
        "cases": len(by_case),
        "cases_passed_every_time": sum(all(r["passed"] for r in rs) for rs in by_case.values()),
        "check_pass_rate": round(sum(c["passed"] for c in checks) / len(checks), 3) if checks else None,
        "total_cost_inr": round(sum(r["cost_inr"] for r in ran), 4),
        "total_wall_s": round(sum(r.get("wall_s") or 0 for r in ran), 1),
        "skipped": skipped,
    }


def write_reports(dest: Path, summary: dict, results: list[dict]) -> None:
    (dest / "eval_results.json").write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False,
                                                       indent=2), encoding="utf-8")
    s = summary
    rate = f"{s['pass_rate']:.0%}" if s["pass_rate"] is not None else "n/a"
    lines = ["# Self-evaluation", "",
             f"- LLM: `{s['llm_model']}`",
             f"- Runs passed: **{s['runs_passed']}/{s['runs']} ({rate})**"
             + (f"; cases passing all {s['repeat']} repeats: {s['cases_passed_every_time']}/{s['cases']}"
                if s["repeat"] > 1 else ""),
             f"- Individual checks passed: {s['check_pass_rate']:.0%}" if s["check_pass_rate"] is not None else "",
             f"- Bodhan cost: ₹{s['total_cost_inr']:.4f}; wall-clock: {s['total_wall_s']:.0f}s", "",
             "| Case | Result | Status | Steps | Cost | Time | Failed checks |", "|---|---|---|---|---|---|---|"]
    for r in results:
        failed = "; ".join(f"{c['type']}: {c['detail']}" for c in r["checks"] if not c["passed"]) or "—"
        if r.get("exit_code") not in (0, 1, "regraded"):
            failed = f"agent exit code {r.get('exit_code')}. {failed}"
        took = f"{r['wall_s']}s" if r.get("wall_s") else "-"
        lines.append(f"| {r['id']} | {'✅' if r['passed'] else '❌'} | {r['status']} | {r['steps']} | "
                     f"₹{r['cost_inr']:.3f} | {took} | {failed.replace('|', '/')} |")
    if s["skipped"]:
        lines += ["", "Skipped:"] + [f"- {k['id']}: {k['why']}" for k in s["skipped"]]
    lines += ["", "Every check is deterministic code over each run's report.json, trace.jsonl and artifacts/; "
                  "see evals/suite.json.", ""]
    (dest / "eval_report.md").write_text("\n".join(x for x in lines if x is not None), encoding="utf-8")
