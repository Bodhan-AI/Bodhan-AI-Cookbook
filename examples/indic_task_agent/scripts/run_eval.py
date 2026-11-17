"""Self-evaluation: run every task in tasks/demo_tasks.json through the agent and report a pass rate.

    python scripts/run_eval.py                     # all tasks
    python scripts/run_eval.py --only 03 06        # tasks whose id starts with 03 or 06
    python scripts/run_eval.py --inputs sample_data/demo --out outputs/eval

Each task runs as `python -m bodhan_agent ...` (exactly how reviewers run it) in its own output
folder; the script then checks report.json/artifacts against the task's `expect` block and writes
outputs/eval/summary.md and summary.json.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check(expect: dict, report: dict, out: Path) -> list[str]:
    problems: list[str] = []
    status = report.get("status")
    if status not in expect.get("status", [status]):
        problems.append(f"status {status!r} not in {expect['status']}")
    arts = report.get("artifacts", [])
    exts = [Path(a["path"]).suffix for a in arts]
    for ext in expect.get("artifact_ext", []):
        if ext not in exts and not (ext in {".md", ".txt"} and {".md", ".txt"} & set(exts)):
            problems.append(f"no {ext} artifact")
    if len(arts) < expect.get("min_artifacts", 0):
        problems.append(f"expected at least {expect['min_artifacts']} artifacts, got {len(arts)}")
    if "max_artifacts" in expect and len(arts) > expect["max_artifacts"]:
        problems.append(f"expected at most {expect['max_artifacts']} artifacts, got {len(arts)}")
    for ext, n in expect.get("max_artifacts_of_ext", {}).items():
        if exts.count(ext) > n:
            problems.append(f"expected at most {n} {ext} artifacts")
    answer = (report.get("answer_en") or "").lower()
    for s in expect.get("answer_contains", []):
        if s.lower() not in answer:
            problems.append(f"answer does not mention {s!r}")
    for s in expect.get("answer_excludes", []):
        if s.lower() in answer:
            problems.append(f"answer mentions {s!r}")
    for s in expect.get("artifact_contains", []):
        texts = [
            (out / a["path"]).read_text(encoding="utf-8", errors="ignore")
            for a in arts
            if Path(a["path"]).suffix in {".md", ".txt", ".srt"}
        ]
        if not any(s in t for t in texts):
            problems.append(f"no text artifact contains {s!r}")
    if expect.get("security_flagged") and not report.get("security_flags"):
        problems.append("prompt-injection text was not flagged")
    if "max_cost" in expect and report.get("cost", {}).get("total_inr", 0) > expect["max_cost"] + 1e-9:
        problems.append("budget exceeded")
    if "audio_seconds" in expect:
        lo, hi = expect["audio_seconds"]
        durs = [a.get("verification", {}).get("duration_s") for a in arts if Path(a["path"]).suffix == ".mp3"]
        if durs and not any(d and lo <= d <= hi for d in durs):
            problems.append(f"audio length {durs} outside {lo}-{hi}s")
    if not report.get("quality_targets", {}).get("budget_respected", True):
        problems.append("budget_respected is false")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inputs", default=str(ROOT / "sample_data" / "demo"))
    ap.add_argument("--out", default=str(ROOT / "outputs" / "eval"))
    ap.add_argument("--only", nargs="*", default=[])
    ap.add_argument("--max-cost-inr", default="20")
    args = ap.parse_args()
    spec = json.loads((ROOT / "tasks" / "demo_tasks.json").read_text(encoding="utf-8"))
    inputs, out_root = Path(args.inputs), Path(args.out)
    rows = []
    for t in spec["tasks"]:
        if args.only and not any(t["id"].startswith(o) for o in args.only):
            continue
        out = out_root / t["id"]
        cmd = [
            sys.executable,
            "-m",
            "bodhan_agent",
            "--task",
            t["task"],
            "--files",
            *[str(inputs / f) for f in t["files"]],
            "--out",
            str(out),
            "--max-cost-inr",
            args.max_cost_inr,
            *t.get("args", []),
        ]
        print(f"\n=== {t['id']}: {t['task']}", flush=True)
        t0 = time.time()
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        elapsed = time.time() - t0
        try:
            report = json.loads((out / "report.json").read_text(encoding="utf-8"))
            problems = check(t["expect"], report, out)
        except (OSError, json.JSONDecodeError) as exc:
            report, problems = {}, [f"no report.json ({exc}); stderr: {proc.stderr[-300:]}"]
        ok = not problems
        rows.append(
            {
                "id": t["id"],
                "task": t["task"],
                "pass": ok,
                "status": report.get("status"),
                "problems": problems,
                "cost_inr": report.get("cost", {}).get("total_inr"),
                "elapsed_s": round(elapsed, 1),
                "steps": report.get("steps", {}).get("tool_calls"),
                "result": str(out / "result.md"),
            }
        )
        print(
            ("PASS" if ok else "FAIL")
            + f"  status={report.get('status')} cost=₹{rows[-1]['cost_inr']} time={elapsed:.0f}s "
            + "; ".join(problems),
            flush=True,
        )
    passed = sum(r["pass"] for r in rows)
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "summary.json").write_text(
        json.dumps({"passed": passed, "total": len(rows), "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        f"# Self-evaluation: {passed}/{len(rows)} passed",
        "",
        "| Task | Pass | Status | Cost (₹) | Time (s) | Steps | Problems |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| [{r['id']}]({r['id']}/result.md) | {'✅' if r['pass'] else '❌'} | {r['status']} | {r['cost_inr']} | {r['elapsed_s']} | {r['steps']} | {'; '.join(r['problems'])} |"
        )
    (out_root / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{passed}/{len(rows)} passed. Summary: {out_root / 'summary.md'}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
