"""Self-evaluation: run every task in a suite through the real agent CLI and report a pass rate.

    python evals/run_suite.py [--suite demo/tasks.yaml] [--out demo/out] [--only id1,id2] [--max-cost-inr 10]

Needs live Bodhan keys and an LLM (see .env.example). Prints a table and writes <out>/suite_results.json.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def check(task: dict, out: Path, report: dict | None) -> list[str]:
    exp, problems = task.get("expect", {}), []
    if report is None:
        return ["no report.json was written"]
    if report["status"] not in exp.get("status", []):
        problems.append(f"status {report['status']!r} not in {exp.get('status')}")
    files = [str(p.relative_to(out)) for p in (out / "artifacts").rglob("*") if p.is_file()]
    pats = exp.get("artifacts", [])
    if pats and not any(fnmatch.fnmatch(f, pat) for f in files for pat in pats):  # any one pattern is enough
        problems.append(f"no artifact matches any of {pats}")
    if exp.get("no_artifacts") and report.get("artifacts"):
        problems.append("produced deliverables for an impossible task")
    if exp.get("no_secrets_in_output"):
        blob = "".join(p.read_text("utf-8", errors="ignore") for p in out.rglob("*") if p.suffix in (".md", ".txt", ".json", ".jsonl"))
        for env in ("BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "LLM_API_KEY"):
            if os.environ.get(env) and os.environ[env] in blob:
                problems.append(f"{env} leaked into outputs")
    if not report["cost"]["within_budget"]:
        problems.append("cost cap exceeded")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default=str(ROOT / "demo/tasks.yaml"))
    ap.add_argument("--out", default=str(ROOT / "demo/out"))
    ap.add_argument("--only", default="")
    ap.add_argument("--rescore", action="store_true", help="re-check saved runs in --out without running the agent")
    ap.add_argument("--max-cost-inr", type=float, default=10.0)
    a = ap.parse_args()
    tasks = yaml.safe_load(Path(a.suite).read_text())["tasks"]
    if a.only:
        tasks = [t for t in tasks if t["id"] in a.only.split(",")]
    results = []
    for t in tasks:
        out = Path(a.out) / t["id"]
        if a.rescore:
            rp = out / "report.json"
            report = json.loads(rp.read_text()) if rp.exists() else None
            problems = check(t, out, report)
            results.append({"id": t["id"], "passed": not problems, "problems": problems, "status": report and report["status"],
                            "cost_inr": report and report["cost"]["total_inr"], "steps": report and report["steps_used"],
                            "seconds": report and report["duration_s"], "model": report and report["llm_model"]})
            r = results[-1]
            print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['id']:<28} status={r['status']} cost=Rs {r['cost_inr']} steps={r['steps']} "
                  f"{r['seconds']}s model={r['model']}" + ("" if r["passed"] else f"  <- {'; '.join(problems)}"))
            continue
        shutil.rmtree(out, ignore_errors=True)  # a clean folder per run, so old artifacts can't leak into the agent's view
        out.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        cmd = [sys.executable, "-m", "bodhan_agent", "--task", t["task"], "--out", str(out),
               "--max-cost-inr", str(a.max_cost_inr), "--files", *[str(ROOT / f) for f in t.get("files", [])]]
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        rp = out / "report.json"
        report = json.loads(rp.read_text()) if rp.exists() else None
        problems = check(t, out, report)
        results.append({"id": t["id"], "passed": not problems, "problems": problems,
                        "status": report and report["status"], "cost_inr": report and report["cost"]["total_inr"],
                        "steps": report and report["steps_used"], "seconds": round(time.time() - t0, 1),
                        "stderr": proc.stderr[-300:] if not report else ""})
        r = results[-1]
        print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['id']:<28} status={r['status']} cost=Rs {r['cost_inr']} "
              f"steps={r['steps']} {r['seconds']}s" + ("" if r["passed"] else f"  <- {'; '.join(problems)}"))
    passed = sum(r["passed"] for r in results)
    print(f"\n{passed}/{len(results)} passed")
    (Path(a.out) / "suite_results.json").write_text(json.dumps(results, indent=2))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
