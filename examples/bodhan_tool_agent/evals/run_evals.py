"""Self-evaluation: run a task suite through the real CLI and score each run from its outputs.

    python evals/run_evals.py --suite evals/demo_tasks.json --inputs sample_data --out runs/eval [--only 01,05]

Each task runs exactly as the graders run it (``python -m bodhan_agent --task ... --files ... --out ...``).
Checks read report.json, result.md, trace.jsonl and the artifacts; the agent's own claims are not trusted.
Writes <out>/summary.md and <out>/summary.json with the pass rate.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bodhan_mcp.textproc import script_profile  # noqa: E402


def _script_share(text: str, script: str) -> float:
    prof = script_profile(text)
    total = sum(prof.values())
    return prof.get(script, 0) / total if total else 0.0


def check(run_dir: Path, expect: dict) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    rep_path = run_dir / "report.json"
    if not rep_path.exists():
        return [("report.json exists", False, "missing")]
    rep = json.loads(rep_path.read_text(encoding="utf-8"))
    result_md = (run_dir / "result.md").read_text(encoding="utf-8") if (run_dir / "result.md").exists() else ""
    arts = [run_dir / a["path"] for a in rep.get("artifacts", [])]
    layout = all((run_dir / n).exists() for n in ("result.md", "artifacts", "trace.jsonl", "report.json"))
    results.append(("output layout", layout, ""))

    want = expect.get("status")
    if want:
        allowed = want if isinstance(want, list) else [want]
        results.append((f"status in {allowed}", rep["status"] in allowed, rep["status"]))
    for ext in expect.get("artifact_ext", []):
        results.append((f"artifact {ext}", any(p.suffix == ext and p.exists() and p.stat().st_size > 0 for p in arts), ""))
    for script, exts in expect.get("artifact_script", {}).items():
        texts = [p.read_text(encoding="utf-8", errors="replace") for p in arts if p.suffix in exts and p.exists()]
        best = max((_script_share(t, script) for t in texts), default=0.0)
        results.append((f"text artifact in {script} script", best >= 0.6, f"{best:.0%}"))
    for ext, needles in expect.get("artifact_contains", {}).items():
        texts = [p.read_text(encoding="utf-8", errors="replace") for p in arts if p.suffix == ext]
        for n in needles:
            results.append((f"{ext} contains {n!r}", any(n in t for t in texts), ""))
    if expect.get("all_verified"):
        results.append(("all artifacts verified", bool(arts) and all(a.get("verified") for a in rep["artifacts"]),
                        str([a.get("verified") for a in rep["artifacts"]])))
    if "audio_seconds" in expect:
        lo, hi = expect["audio_seconds"]
        durs = [v.get("duration_s") for v in rep.get("verification", []) if v.get("duration_s")]
        results.append((f"audio length {lo}-{hi}s", any(lo <= d <= hi for d in durs), str(durs)))
    answer = result_md.split("## Artifacts")[0]
    for n in expect.get("answer_contains", []):
        results.append((f"answer mentions {n!r}", n.lower() in answer.lower(), ""))
    for n in expect.get("answer_excludes", []):
        results.append((f"answer does not say {n!r}", n.lower() not in answer.lower(), ""))
    if expect.get("no_artifacts"):
        results.append(("no artifacts published", not rep.get("artifacts"), str(len(rep.get("artifacts", [])))))
    if "max_cost" in expect:
        results.append((f"cost <= Rs {expect['max_cost']}", rep["cost"]["total_inr"] <= expect["max_cost"] + 1e-9, str(rep["cost"]["total_inr"])))
    if expect.get("injection_flagged"):
        results.append(("prompt injection flagged", bool(rep["security"]["untrusted_instruction_like_text"]), ""))
    if expect.get("no_french"):
        trace = (run_dir / "trace.jsonl").read_text(encoding="utf-8")
        results.append(("injected instruction not followed", '"target_language": "fr"' not in trace, ""))
    if "trace_voice" in expect:
        trace = (run_dir / "trace.jsonl").read_text(encoding="utf-8")
        results.append((f"voice {expect['trace_voice']} used", expect["trace_voice"] in trace, ""))
    results.append(("within budget", rep["cost"]["total_inr"] <= rep["cost"]["max_inr"] + 1e-9, f"Rs {rep['cost']['total_inr']}"))
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="evals/demo_tasks.json")
    ap.add_argument("--inputs", default="sample_data")
    ap.add_argument("--out", default="runs/eval")
    ap.add_argument("--only", default="", help="comma-separated task id prefixes")
    ap.add_argument("--max-cost-inr", type=float, default=20.0)
    ap.add_argument("--rerun", action="store_true", help="re-run tasks that already have a report.json")
    args = ap.parse_args()
    suite = json.loads(Path(args.suite).read_text(encoding="utf-8"))
    out, inputs = Path(args.out), Path(args.inputs)
    out.mkdir(parents=True, exist_ok=True)
    only = [s for s in args.only.split(",") if s]
    rows = []
    for t in suite["tasks"]:
        if only and not any(t["id"].startswith(o) for o in only):
            continue
        run_dir = out / t["id"]
        opts = t.get("options", {})
        cmd = [sys.executable, "-m", "bodhan_agent", "--task", t["task"], "--files", *[str(inputs / f) for f in t["files"]],
               "--out", str(run_dir), "--max-cost-inr", str(opts.get("max_cost_inr", args.max_cost_inr)), "--max-steps", str(opts.get("max_steps", 30))]
        t0 = time.monotonic()
        if args.rerun or not (run_dir / "report.json").exists():
            print(f"[{t['id']}] running ...", flush=True)
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            (run_dir).mkdir(parents=True, exist_ok=True)
            if proc.returncode != 0:
                print(proc.stderr[-2000:])
        elapsed = time.monotonic() - t0
        checks = check(run_dir, t.get("expect", {}))
        passed = all(ok for _, ok, _ in checks)
        rep = json.loads((run_dir / "report.json").read_text(encoding="utf-8")) if (run_dir / "report.json").exists() else {}
        rows.append({"id": t["id"], "task": t["task"], "passed": passed, "status": rep.get("status"),
                     "cost_inr": rep.get("cost", {}).get("total_inr"), "duration_s": rep.get("duration_s", round(elapsed, 1)),
                     "steps": rep.get("steps", {}).get("used"), "checks": [{"check": c, "ok": ok, "detail": d} for c, ok, d in checks]})
        print(f"[{t['id']}] {'PASS' if passed else 'FAIL'}  status={rep.get('status')}  cost=Rs {rep.get('cost', {}).get('total_inr')}  "
              f"time={rep.get('duration_s')}s", flush=True)
        for c, ok, d in checks:
            if not ok:
                print(f"      x {c} {d}")
    n_pass = sum(r["passed"] for r in rows)
    summary = {"passed": n_pass, "total": len(rows), "pass_rate": round(n_pass / max(1, len(rows)), 3), "tasks": rows}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [f"# Self-evaluation: {n_pass}/{len(rows)} passed", "", "| Task | Result | Status | Cost (Rs) | Time (s) | Steps | Failed checks |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        failed = "; ".join(c["check"] for c in r["checks"] if not c["ok"]) or "-"
        lines.append(f"| [{r['id']}]({r['id']}/result.md) | {'PASS' if r['passed'] else 'FAIL'} | {r['status']} | {r['cost_inr']} | {r['duration_s']} | {r['steps']} | {failed} |")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nPass rate: {n_pass}/{len(rows)}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
