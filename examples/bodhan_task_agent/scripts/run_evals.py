"""Self-evaluation: run the demo task suite through the real agent command and report a pass rate.

    python scripts/run_evals.py                       # all tasks -> runs/<id>/ and evals/results.md
    python scripts/run_evals.py --only 03 06          # task ids starting with these prefixes

Each task runs as `python -m bodhan_agent ...`, exactly as the evaluators will run it. A task
passes when its report.json meets the expectations in evals/demo_tasks.json (status, artifact
types, honesty and budget checks). Reruns are cheap: Bodhan results are cached on disk.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_ROOT_PREFIX = re.compile(re.escape(str(ROOT)[:min(len(str(ROOT)), 10)]) + r"[^\s,;)\]`'\"]*")


def scrub(out: Path) -> None:
    """Replace this machine's absolute paths with <recipe> in committed text outputs (privacy, portability)."""
    if not out.exists():
        return
    for p in out.rglob("*"):
        if p.is_file() and p.suffix in {".md", ".json", ".jsonl", ".srt", ".vtt", ".txt"}:
            body = p.read_text(encoding="utf-8", errors="replace")
            new = body.replace(str(ROOT), "<recipe>")
            # Summaries truncate long values, which can leave a cut-off prefix of ROOT behind.
            new = _ROOT_PREFIX.sub(lambda m: "<recipe>…" if str(ROOT).startswith(m.group(0)) else "<path>", new)
            if new != body:
                p.write_text(new, encoding="utf-8")


def check(rep: dict, out: Path, exp: dict) -> list[str]:
    fails: list[str] = []
    if rep["status"] not in exp.get("status", [rep["status"]]):
        fails.append(f"status {rep['status']} not in {exp['status']}")
    files = [a["file"] for a in rep.get("artifacts", [])]
    for alt in exp.get("artifact_suffixes", []):
        if not any(f.endswith(tuple(alt.split("|"))) for f in files):
            fails.append(f"no artifact ending in {alt}")
    if exp.get("no_artifacts") and files:
        fails.append(f"expected no artifacts, got {files}")
    if exp.get("no_audio") and any(f.endswith((".mp3", ".wav")) for f in files):
        fails.append("audio produced despite budget")
    if "max_cost_inr" in exp and rep["cost_inr"]["total"] > exp["max_cost_inr"] + 1e-9:
        fails.append(f"cost ₹{rep['cost_inr']['total']} > ₹{exp['max_cost_inr']}")
    if exp.get("injection_flagged") and not rep.get("prompt_injection"):
        fails.append("prompt injection not flagged")
    answer = (rep.get("answer") or "").lower()
    for word in exp.get("answer_mentions", []):
        if word.lower() not in answer:
            fails.append(f"answer does not mention {word!r}")
    if exp.get("artifact_has_table"):
        mds = [out / f for f in files if f.endswith(".md")]
        if not any("|" in p.read_text(encoding="utf-8") for p in mds):
            fails.append("no Markdown table in artifacts")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="*", default=[])
    ap.add_argument("--runs", default=str(ROOT / "demo_runs"))
    args = ap.parse_args()
    suite = json.loads((ROOT / "evals" / "demo_tasks.json").read_text(encoding="utf-8"))["tasks"]
    rows, results = [], []
    for t in suite:
        if args.only and not any(t["id"].startswith(p) for p in args.only):
            continue
        out = Path(args.runs) / t["id"]
        cmd = [sys.executable, "-m", "bodhan_agent", "--task", t["task"], "--files", *t["files"], "--out", str(out),
               *t.get("args", [])]
        print(f"→ {t['id']}: {t['task']}", flush=True)
        t0 = time.monotonic()
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        wall = time.monotonic() - t0
        scrub(out)
        try:
            rep = json.loads((out / "report.json").read_text(encoding="utf-8"))
            fails = check(rep, out, t["expect"])
        except FileNotFoundError:
            rep, fails = {"status": "no_report", "cost_inr": {"total": 0}}, [f"no report (exit {proc.returncode}): {proc.stderr[-300:]}"]
        ok = not fails
        results.append({"id": t["id"], "passed": ok, "status": rep["status"], "cost_inr": rep["cost_inr"]["total"],
                        "time_s": round(wall, 1), "failures": fails})
        rows.append(f"| {t['id']} | {t['task']} | {rep['status']} | {'PASS' if ok else 'FAIL'} | "
                    f"₹{rep['cost_inr']['total']:.3f} | {wall:.0f}s | [result]({out.relative_to(ROOT)}/result.md) · "
                    f"[trace]({out.relative_to(ROOT)}/trace.jsonl) | {'; '.join(fails)[:120]} |")
        print(f"   {'PASS' if ok else 'FAIL'} status={rep['status']} cost=₹{rep['cost_inr']['total']:.3f} {wall:.0f}s "
              + ("; ".join(fails) if fails else ""), flush=True)
    passed = sum(r["passed"] for r in results)
    md = [f"# Demo task results: {passed}/{len(results)} passed", "",
          "| Task | Prompt | Status | Check | Cost | Time | Outputs | Notes |", "|---|---|---|---|---|---|---|---|", *rows, ""]
    (ROOT / "evals" / "results.md").write_text("\n".join(md), encoding="utf-8")
    (ROOT / "evals" / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{passed}/{len(results)} passed. See evals/results.md")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
