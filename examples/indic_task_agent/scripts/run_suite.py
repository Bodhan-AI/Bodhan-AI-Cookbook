"""Run the demo/self-evaluation suite and score it.

    python scripts/run_suite.py [--ids d1_chapter_tamil_audio,d6_video_french] [--out outputs/suite] [--score-only]

Each task runs exactly like a reviewer would run it (`python -m bodhan_agent ...`,
one output folder per task). Scoring uses only generic checks on report.json
(status, deliverable kinds/languages, answer text); it cannot see translation
quality, which needs a human/native-speaker review. Writes outputs/suite/summary.json
and summary.md.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def score(task: dict, report: dict, out: Path) -> list[str]:
    """Return a list of failed checks (empty = pass)."""
    exp, fails = task.get("expect", {}), []
    if report["status"] not in exp.get("status", [report["status"]]):
        fails.append(f"status {report['status']} not in {exp['status']}")
    dels = report.get("deliverables", [])
    kinds = [d["kind"] for d in dels]
    for k in exp.get("deliverable_kinds", []):
        if k not in kinds:
            fails.append(f"no {k} deliverable")
    for kind, lang in exp.get("languages", {}).items():
        langs = [d.get("language") for d in dels if d["kind"] == kind]
        if langs and lang not in langs:
            fails.append(f"{kind} language {langs} != {lang}")
    if exp.get("no_deliverables") and dels:
        fails.append(f"impossible task produced deliverables {[d['path'] for d in dels]}")
    if exp.get("min_deliverables") and len(dels) < exp["min_deliverables"]:
        fails.append(f"{len(dels)} deliverables < {exp['min_deliverables']}")
    answer = report.get("final_answer", "")
    for s in exp.get("answer_contains", []):
        if s.lower() not in answer.lower():
            fails.append(f"answer lacks {s!r}")
    for s in exp.get("answer_excludes", []):
        if s.lower() in answer.lower():
            fails.append(f"answer contains {s!r}")
    if exp.get("deliverable_has_table"):
        from bodhan_mcp.textutil import table_shapes

        tables = [table_shapes((out / d["path"]).read_text(encoding="utf-8")) for d in dels if d["kind"] == "translation"]
        if not any(tables):
            fails.append("translation has no table")
    if exp.get("voice_gender"):
        from bodhan_mcp.languages import ALL_VOICES

        notes = " ".join(n for d in dels for n in d.get("notes", []))
        voices = [v for v in ALL_VOICES if f"voice {v}" in notes]
        if voices and ALL_VOICES[voices[0]][1] != exp["voice_gender"]:
            fails.append(f"voice {voices[0]} is not {exp['voice_gender']}")
    if exp.get("any_language") and not any(d.get("language") == exp["any_language"] for d in dels):
        fails.append(f"no deliverable in {exp['any_language']}")
    if exp.get("max_cost_inr") is not None and report["cost"]["total_counted_inr"] > exp["max_cost_inr"] + 1e-9:
        fails.append("budget exceeded")
    if not report["verification"]["all_checked"]:
        fails.append("unverified translation/audio")
    return fails


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", default="")
    ap.add_argument("--out", default=str(ROOT / "outputs" / "suite"))
    ap.add_argument("--score-only", action="store_true", help="re-score existing outputs without running")
    ap.add_argument("--env-file", default=None)
    a, extra = ap.parse_known_args()
    tasks = json.loads((ROOT / "evaluation" / "tasks.json").read_text(encoding="utf-8"))["tasks"]
    if a.ids:
        wanted = set(a.ids.split(","))
        tasks = [t for t in tasks if t["id"] in wanted]
    base = Path(a.out)
    rows = []
    for t in tasks:
        out = base / t["id"]
        if not a.score_only:
            cmd = [sys.executable, "-m", "bodhan_agent", "--task", t["task"], "--files", *[str(ROOT / f) for f in t["files"]], "--out", str(out),
                   "--max-steps", str(t.get("max_steps", 30)), "--max-cost-inr", str(t.get("max_cost_inr", 20))]
            if a.env_file:
                cmd += ["--env-file", a.env_file]
            cmd += extra
            t0 = time.monotonic()
            proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
            print(f"[{t['id']}] exit={proc.returncode} {time.monotonic() - t0:.0f}s :: {proc.stdout.strip()[-200:]}", flush=True)
            if proc.returncode != 0:
                print(proc.stderr[-2000:], file=sys.stderr)
        rp = out / "report.json"
        if not rp.exists():
            rows.append({"id": t["id"], "pass": False, "fails": ["no report.json"], "status": None})
            continue
        report = json.loads(rp.read_text(encoding="utf-8"))
        fails = score(t, report, out)
        rows.append({"id": t["id"], "demo": t.get("demo", False), "impossible": t.get("impossible", False), "task": t["task"], "pass": not fails,
                     "fails": fails, "status": report["status"], "elapsed_s": report["elapsed_s"], "steps": report["steps"]["used"],
                     "bodhan_inr": report["cost"]["total_counted_inr"], "llm_tokens": report["cost"]["llm"]["prompt_tokens"] + report["cost"]["llm"]["completion_tokens"],
                     "latency_target_met": report["quality_targets"]["latency"]["met"], "latency_kind": report["quality_targets"]["latency"]["kind"],
                     "result": str((out / "result.md").relative_to(ROOT)).replace("\\", "/"), "trace": str((out / "trace.jsonl").relative_to(ROOT)).replace("\\", "/")})
    passed = sum(r["pass"] for r in rows)
    summary = {"passed": passed, "total": len(rows), "pass_rate": round(passed / max(len(rows), 1), 3), "tasks": rows}
    base.mkdir(parents=True, exist_ok=True)
    (base / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"# Suite: {passed}/{len(rows)} passed automatic checks", "",
             "| Task | Status | Pass | Time (s) | Steps | Bodhan ₹ | Latency target | Notes |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r.get('status')} | {'✅' if r['pass'] else '❌'} | {r.get('elapsed_s', '')} | {r.get('steps', '')} | "
                     f"{r.get('bodhan_inr', '')} | {r.get('latency_kind', '')} {'met' if r.get('latency_target_met') else 'missed'} | {'; '.join(r['fails'])} |")
    (base / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
