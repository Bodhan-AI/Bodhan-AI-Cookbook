"""Run the demo task suite through the agent CLI and score each run against its expectation.

    python scripts/self_eval.py                              # every task in tasks/demo_tasks.json
    python scripts/self_eval.py --only bengali_read_aloud missing_file
    python scripts/self_eval.py --dry-run                    # print the commands only

Each task runs as `python -m bodhan_agent ... --out outputs/<id>` in a subprocess, exactly as a
reviewer would run it. Scoring reads only outputs/<id>/report.json, result.md and artifacts/, so it
judges what the agent wrote, not what it intended. Summary goes to stdout and outputs/self_eval.json.

Checks are structural (status, artifact kinds, script of the output, table present, budget kept,
no artifacts on impossible tasks). They do not judge translation quality; that needs a reader.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

KIND_BY_SUFFIX = {
    ".mp3": "audio", ".wav": "audio", ".ogg": "audio", ".flac": "audio",
    ".srt": "subtitles", ".vtt": "subtitles",
    ".md": "markdown", ".txt": "text", ".json": "data",
}


def artifact_kinds(path: Path) -> set[str]:
    kind = KIND_BY_SUFFIX.get(path.suffix.lower(), "other")
    # A Markdown file is also a text artifact; the reverse is not true.
    return {kind, "text"} if kind == "markdown" else {kind}


def list_artifacts(out_dir: Path) -> list[Path]:
    art = out_dir / "artifacts"
    return sorted(p for p in art.rglob("*") if p.is_file()) if art.is_dir() else []


def _first(d: dict, *keys, default=None):
    for k in keys:
        cur = d
        for part in k.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur is not None:
            return cur
    return default


def load_run(out_dir: Path) -> dict:
    report_path = out_dir / "report.json"
    report: dict = {}
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            report = {"_error": "report.json is not valid JSON"}
    result_md = (out_dir / "result.md").read_text(encoding="utf-8") if (out_dir / "result.md").exists() else ""
    answer = _first(report, "answer", "final_answer", "result.answer", default="") or ""
    assumptions = _first(report, "assumptions", "plan.assumptions", default=[]) or []
    return {
        "report": report,
        "report_text": json.dumps(report, ensure_ascii=False).lower(),
        "result_md": result_md,
        "answer": str(answer),
        "assumptions": assumptions,
        "status": _first(report, "status", default=None),
        "cost_inr": float(_first(report, "total_cost_inr", "cost_inr", "cost.total_inr", default=0.0) or 0.0),
        "latency_s": _first(report, "elapsed_s", "total_time_s", "duration_s", "latency_s", default=None),
        "artifacts": list_artifacts(out_dir),
    }


def _searchable(run: dict) -> str:
    parts = [run["answer"], run["result_md"], " ".join(map(str, run["assumptions"]))]
    return "\n".join(parts).lower()


def _detect(text: str) -> tuple[str | None, list[str]]:
    from bodhan_mcp.languages import detect_language

    # Subtitle files: drop cue numbers and timestamps before guessing.
    text = re.sub(r"^\d+\s*$|^[\d:,.]+ --> [\d:,.]+.*$|^WEBVTT.*$", "", text, flags=re.M)
    g = detect_language(text)
    return g.language, list(g.candidates)


def run_check(check: dict, run: dict) -> tuple[bool, str]:
    kind = check["type"]
    arts = run["artifacts"]
    if kind == "no_artifacts":
        return (not arts, f"{len(arts)} artifact(s)")
    if kind == "no_artifact_kind":
        bad = [p.name for p in arts if check["kind"] in artifact_kinds(p)]
        return (not bad, ", ".join(bad) or "none")
    if kind == "artifact_script":
        files = [p for p in arts if check["kind"] in artifact_kinds(p)]
        if not files:
            return False, f"no {check['kind']} artifact"
        seen = []
        for p in files:
            lang, cands = _detect(p.read_text(encoding="utf-8", errors="replace"))
            seen.append(f"{p.name}={lang}")
            if lang == check["lang"] or check["lang"] in cands:
                return True, ", ".join(seen)
        return False, ", ".join(seen)
    if kind == "markdown_table":
        best = 0
        for p in arts:
            if "markdown" in artifact_kinds(p):
                rows = [ln for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()
                        if ln.strip().startswith("|") and not re.fullmatch(r"\|?[\s:|-]+\|?", ln.strip())]
                best = max(best, len(rows))
        need = check.get("min_rows", 2)
        return best >= need, f"{best} table row(s), need {need}"
    if kind == "mentions_all":
        text = _searchable(run)
        missing = [v for v in check["values"] if v.lower() not in text]
        return not missing, f"missing {missing}" if missing else "all present"
    if kind == "mentions_any":
        text = _searchable(run)
        hit = [v for v in check["values"] if v.lower() in text]
        return bool(hit), f"found {hit}" if hit else f"none of {check['values']}"
    if kind == "report_mentions":
        text = run["report_text"] + "\n" + run["result_md"].lower()
        hit = [v for v in check["values"] if v.lower() in text]
        return bool(hit), f"found {hit}" if hit else f"none of {check['values']}"
    if kind == "answer_not_equal":
        ok = run["answer"].strip().strip(".").lower() != check["value"].lower()
        return ok, "answer differs" if ok else "answer equals injected text"
    if kind == "assumptions_recorded":
        n = len(run["assumptions"])
        has_section = "assumption" in run["result_md"].lower()
        return n > 0 or has_section, f"{n} assumption(s) in report"
    if kind == "verification_passed":
        ver = _first(run["report"], "verification", "verifications", default=None)
        if ver is None:
            return False, "no verification block in report.json"
        items = ver if isinstance(ver, list) else list(ver.values()) if isinstance(ver, dict) else []
        if isinstance(ver, dict) and "passed" in ver:
            items = [ver]
        failed = [i for i in items if isinstance(i, dict) and i.get("passed") is False]
        return bool(items) and not failed, f"{len(items)} check(s), {len(failed)} failed"
    if kind == "max_latency_s":
        lat = run["latency_s"]
        return (lat is not None and lat <= check["value"]), f"{lat}s (target {check['value']}s)"
    return False, f"unknown check type {kind!r}"


def flag_value(flags: list[str], name: str) -> str | None:
    return flags[flags.index(name) + 1] if name in flags and flags.index(name) + 1 < len(flags) else None


def evaluate(task: dict, run: dict, budget: float | None) -> dict:
    exp = task["expectation"]
    results = []
    want = exp["status"] if isinstance(exp["status"], list) else [exp["status"]]
    results.append(("status", run["status"] in want, f"got {run['status']}, want {'|'.join(want)}"))
    kinds = set().union(*(artifact_kinds(p) for p in run["artifacts"])) if run["artifacts"] else set()
    for k in exp.get("artifact_kinds", []):
        results.append((f"artifact:{k}", k in kinds, "present" if k in kinds else "missing"))
    if budget is not None:
        results.append(("budget", run["cost_inr"] <= budget + 1e-9, f"INR {run['cost_inr']:.3f} <= {budget}"))
    if "impossible" in want and len(want) == 1:
        results.append(("no_fabrication", not run["artifacts"], f"{len(run['artifacts'])} artifact(s)"))
    for check in exp.get("checks", []):
        ok, detail = run_check(check, run)
        results.append((check["type"], ok, detail))
    return {
        "passed": all(ok for _, ok, _ in results),
        "checks": [{"name": n, "passed": ok, "detail": d} for n, ok, d in results],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tasks", type=Path, default=ROOT / "tasks" / "demo_tasks.json")
    parser.add_argument("--out", type=Path, default=ROOT / "outputs")
    parser.add_argument("--only", nargs="*", help="task ids to run")
    parser.add_argument("--timeout", type=float, default=900, help="wall-clock limit per task, seconds")
    parser.add_argument("--score-only", action="store_true", help="re-score existing outputs without running")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    suite = json.loads(args.tasks.read_text(encoding="utf-8"))
    tasks = [t for t in suite["tasks"] if not args.only or t["id"] in args.only]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT / "src"), os.environ.get("PYTHONPATH")]))}

    rows = []
    for task in tasks:
        out_dir = (args.out / task["id"]).resolve()
        flags = list(suite.get("default_flags", []))
        for i in range(0, len(task.get("flags", [])), 2):
            name, value = task["flags"][i], task["flags"][i + 1]
            if name in flags:
                flags[flags.index(name) + 1] = value
            else:
                flags += [name, value]
        cmd = [sys.executable, "-m", "bodhan_agent", "--task", task["task"], "--out", str(out_dir), *flags]
        for f in task["files"]:
            cmd += ["--files", str(ROOT / f)]
        if args.dry_run:
            print(" ".join(f'"{c}"' if " " in c else c for c in cmd))
            continue

        wall = None
        exit_code = None
        if not args.score_only:
            print(f"-> {task['id']} ...", flush=True)
            t0 = time.monotonic()
            try:
                proc = subprocess.run(cmd, cwd=ROOT, env=env, timeout=args.timeout, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace")
                exit_code = proc.returncode
                (out_dir).mkdir(parents=True, exist_ok=True)
                (out_dir / "agent_stderr.log").write_text(proc.stderr[-20000:], encoding="utf-8")
            except subprocess.TimeoutExpired:
                exit_code = "timeout"
            wall = round(time.monotonic() - t0, 1)

        run = load_run(out_dir)
        if run["latency_s"] is None:
            run["latency_s"] = wall
        budget = flag_value(flags, "--max-cost-inr")
        verdict = evaluate(task, run, float(budget) if budget else None)
        rows.append({
            "id": task["id"],
            "task": task["task"],
            "passed": verdict["passed"],
            "status": run["status"],
            "cost_inr": round(run["cost_inr"], 4),
            "latency_s": run["latency_s"],
            "exit_code": exit_code,
            "artifacts": [p.relative_to(out_dir).as_posix() for p in run["artifacts"]],
            "checks": verdict["checks"],
            "out_dir": out_dir.relative_to(ROOT).as_posix() if out_dir.is_relative_to(ROOT) else str(out_dir),
        })

    if args.dry_run:
        return 0

    print()
    print(f"{'task':28} {'result':6} {'status':11} {'cost INR':>9} {'time s':>7}  failed checks")
    print("-" * 100)
    for r in rows:
        failed = [c["name"] + " (" + c["detail"] + ")" for c in r["checks"] if not c["passed"]]
        lat = f"{r['latency_s']:.1f}" if isinstance(r["latency_s"], (int, float)) else "-"
        print(f"{r['id']:28} {'PASS' if r['passed'] else 'FAIL':6} {str(r['status']):11} {r['cost_inr']:>9.3f} "
              f"{lat:>7}  {'; '.join(failed)}")
    passed = sum(r["passed"] for r in rows)
    total_cost = sum(r["cost_inr"] for r in rows)
    print("-" * 100)
    print(f"pass rate {passed}/{len(rows)}   total cost INR {total_cost:.3f}")

    args.out.mkdir(parents=True, exist_ok=True)
    summary = {
        "suite": args.tasks.resolve().relative_to(ROOT).as_posix()
        if args.tasks.resolve().is_relative_to(ROOT)
        else str(args.tasks),
        "pass_rate": round(passed / len(rows), 3) if rows else 0.0,
        "passed": passed,
        "total": len(rows),
        "total_cost_inr": round(total_cost, 4),
        "tasks": rows,
    }
    (args.out / "self_eval.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {args.out / 'self_eval.json'}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
