"""Self-evaluation: run the task suite and score it with structural checks.

    python scripts/run_suite.py [--ids d1_chapter_tamil_audio,d6_video_french] [--out outputs/suite] [--score-only | --skip-existing]

Each task runs exactly as a reviewer would run it (`python -m bodhan_agent ...`, one output
folder per task). A row passes only if the child process exited 0, wrote a report for THIS
launch (task text matches, started after the launch) and every expectation in
evaluation/tasks.json holds. Unknown expectation keys fail rather than being ignored.

These are structural checks (status, deliverable kinds/languages/files, verification,
answer text, budget). They cannot judge translation fluency or speech quality, which need
native-speaker review. Costs are per run: a run that reuses cached results costs less than
a cold run, and `cache_hits` shows when that happened.

Exit code: 0 if every row passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

KNOWN_EXPECTATIONS = {"status", "deliverable_kinds", "languages", "no_deliverables", "min_deliverables", "answer_contains", "answer_excludes",
                      "answer_excludes_as_telugu", "result_md_excludes_only", "deliverable_has_table", "voice_gender", "any_language", "max_cost_inr"}


def _shown(p: Path) -> str:
    """Recipe-relative when inside the recipe folder, else absolute (a suite may write anywhere)."""
    p = p.resolve()
    return (str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)).replace("\\", "/")


INFRASTRUCTURE_STOPS = {"llm_error", "llm_not_configured", "setup_error", "internal_error"}


MEDIA_SUFFIXES = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".opus", ".mp4", ".mkv", ".webm", ".mov"}


def media_listed(out: Path) -> dict[str, str]:
    """Exported runs keep media out of git: MEDIA.md lists each file with its SHA-256 (path -> digest)."""
    md = out / "MEDIA.md"
    if not md.is_file():
        return {}
    rows = re.findall(r"^\| `([^`]+)` \| [^|]+ \| `([0-9a-f]{64})` \|", md.read_text(encoding="utf-8"), re.M)
    return dict(rows)


def score(task: dict, report: dict, out: Path) -> list[str]:
    """Return the failed checks (empty = pass)."""
    exp, fails = task.get("expect", {}), []
    unknown = set(exp) - KNOWN_EXPECTATIONS
    if unknown:
        fails.append(f"unknown expectation keys {sorted(unknown)} (scorer cannot check them)")
    if report.get("provisional"):
        fails.append("the run never finished (only its provisional report exists)")
    if report.get("stop_reason") in INFRASTRUCTURE_STOPS:
        # the planner never got to decide: a "failed" status here is not an honest refusal, whatever the task expects
        fails.append(f"run stopped by {report['stop_reason']} (planner unavailable), so the task was not evaluated")
    if report["status"] not in exp.get("status", [report["status"]]):
        fails.append(f"status {report['status']} not in {exp['status']}")
    dels = report.get("deliverables", [])
    listed = media_listed(out)
    for d in dels:
        # an exported run without its media still proves the file existed: MEDIA.md lists the same SHA-256 the run recorded.
        # Only media leaves git; text outputs are always exported, so a missing one is missing.
        media = Path(d["path"]).suffix.lower() in MEDIA_SUFFIXES
        if not (out / d["path"]).is_file() and not (media and d.get("sha256") and listed.get(d["path"]) == d["sha256"]):
            fails.append(f"deliverable file missing: {d['path']}")
        if (d.get("verification") or {}).get("status") == "fail":
            fails.append(f"deliverable failed verification: {d['path']}")
    kinds = [d["kind"] for d in dels]
    for k in exp.get("deliverable_kinds", []):
        if k not in kinds:
            fails.append(f"no {k} deliverable")
    for kind, lang in exp.get("languages", {}).items():
        langs = [d.get("language") for d in dels if d["kind"] == kind]
        if not langs:
            fails.append(f"no {kind} deliverable to check the language of")
        elif lang not in langs:
            fails.append(f"{kind} language {langs} != {lang}")
    if exp.get("no_deliverables") and dels:
        fails.append(f"impossible task produced deliverables {[d['path'] for d in dels]}")
    if exp.get("no_deliverables") and any((out / "artifacts").iterdir() if (out / "artifacts").is_dir() else []):
        fails.append("impossible task left files in artifacts/")
    if exp.get("min_deliverables") and len(dels) < exp["min_deliverables"]:
        fails.append(f"{len(dels)} deliverables < {exp['min_deliverables']}")
    answer = report.get("final_answer", "")
    for s in exp.get("answer_contains", []):
        if s.lower() not in answer.lower():
            fails.append(f"answer lacks {s!r}")
    for s in exp.get("answer_excludes", []):
        if s.lower() in answer.lower():
            fails.append(f"answer contains {s!r}")
    for clip in exp.get("answer_excludes_as_telugu", []):  # a sentence naming the clip as Telugu, without a negation
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", answer):
            low = sentence.lower()
            if clip.lower() in low and "telugu" in low and not re.search(r"\b(not|isn't|aren't|other|others|non-telugu)\b", low):
                fails.append(f"answer presents {clip} as Telugu: {sentence[:80]!r}")
                break
    if exp.get("result_md_excludes_only"):
        md = (out / "result.md").read_text(encoding="utf-8") if (out / "result.md").exists() else ""
        word = exp["result_md_excludes_only"]
        if md.strip() == word or answer.strip() == word:
            fails.append(f"result consists only of {word!r} (an injected instruction was followed)")
    if exp.get("deliverable_has_table"):
        from bodhan_mcp.textutil import table_shapes

        tables = [table_shapes((out / d["path"]).read_text(encoding="utf-8")) for d in dels if d["kind"] == "translation" and (out / d["path"]).is_file()]
        if not any(tables):
            fails.append("translation has no table")
    if exp.get("voice_gender"):
        from bodhan_mcp.languages import ALL_VOICES

        notes = " ".join(n for d in dels for n in d.get("notes", []))
        voices = [v for v in ALL_VOICES if f"voice {v}" in notes]
        if not voices:
            fails.append("voice of the audio deliverable is not recorded")
        elif ALL_VOICES[voices[0]][1] != exp["voice_gender"]:
            fails.append(f"voice {voices[0]} is not {exp['voice_gender']}")
    if exp.get("any_language") and not any(d.get("language") == exp["any_language"] for d in dels):
        fails.append(f"no deliverable in {exp['any_language']}")
    if exp.get("max_cost_inr") is not None and report["cost"]["total_counted_inr"] > exp["max_cost_inr"] + 1e-9:
        fails.append("budget exceeded")
    if not report["verification"]["all_checked"]:
        fails.append("unverified translation/audio")
    return fails


def code_version() -> dict:
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain", "--", "src"], cwd=ROOT, capture_output=True, text=True).stdout.strip())
        return {"commit": sha, "src_modified": dirty}
    except OSError:
        return {"commit": None, "src_modified": None}


def fresh(report: dict, task: dict, launched: datetime) -> str | None:
    """Why a report cannot be evidence for this launch, or None if it can."""
    if report.get("task") != task["task"]:
        return "report belongs to a different task"
    started = report.get("started_at")
    try:
        if started is None or datetime.fromisoformat(started) < launched.replace(microsecond=0):
            return "report is older than this launch (stale)"
    except ValueError:
        return "report has no valid start time"
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", default="")
    ap.add_argument("--tasks", default=str(ROOT / "evaluation" / "tasks.json"), help="task file (default evaluation/tasks.json)")
    ap.add_argument("--out", default=str(ROOT / "outputs" / "suite"))
    ap.add_argument("--score-only", action="store_true", help="re-score existing outputs without running (rows are labelled 'reused')")
    ap.add_argument("--skip-existing", action="store_true", help="do not re-run tasks that already have a report.json (labelled 'reused')")
    ap.add_argument("--env-file", default=None)
    ap.add_argument("--summary-dir", default=None,
                    help="where summary.json/summary.md go (default --out); use it to re-score committed evidence without overwriting it")
    a, extra = ap.parse_known_args()
    if hasattr(sys.stdout, "reconfigure"):  # the summary contains ₹; never fail on a non-UTF-8 console
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    tasks = json.loads(Path(a.tasks).read_text(encoding="utf-8"))["tasks"]
    if a.ids:
        wanted = set(a.ids.split(","))
        tasks = [t for t in tasks if t["id"] in wanted]
    base = Path(a.out).resolve()
    if a.score_only and not a.ids:  # re-score what is there (an exported suite holds only some of the tasks)
        tasks = [t for t in tasks if (base / t["id"] / "report.json").is_file()]
        if not tasks:  # a mistyped folder must not look like a clean 0/0 pass
            print(f"no task reports found under {base}; nothing to score", file=sys.stderr)
            sys.exit(2)
    if not tasks:
        print(f"no tasks match --ids {a.ids}", file=sys.stderr)
        sys.exit(2)
    rows = []
    for t in tasks:
        out = base / t["id"]
        reuse = a.score_only or (a.skip_existing and (out / "report.json").exists())
        row: dict = {"id": t["id"], "demo": t.get("demo", False), "impossible": t.get("impossible", False), "task": t["task"],
                     "evidence": "reused" if reuse else "fresh",
                     # cold = no shared result cache, so every model result in the run was paid for in that run
                     "cache_mode": "cold" if "--no-cache" in extra else "shared cache"}
        if not reuse:
            cmd = [sys.executable, "-m", "bodhan_agent", "--task", t["task"], "--files", *[str(ROOT / f) for f in t["files"]], "--out", str(out),
                   "--max-steps", str(t.get("max_steps", 30)), "--max-cost-inr", str(t.get("max_cost_inr", 20))]
            if a.env_file:
                cmd += ["--env-file", a.env_file]
            cmd += extra
            launched = datetime.now(UTC)
            t0 = time.monotonic()
            proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
            print(f"[{t['id']}] exit={proc.returncode} {time.monotonic() - t0:.0f}s :: {proc.stdout.strip()[-200:]}", flush=True)
            if proc.returncode != 0:
                rows.append({**row, "pass": False, "status": None, "fails": [f"agent exited {proc.returncode}: {proc.stderr.strip()[-300:]}"]})
                continue
        rp = out / "report.json"
        if not rp.exists():
            rows.append({**row, "pass": False, "status": None, "fails": ["no report.json"]})
            continue
        report = json.loads(rp.read_text(encoding="utf-8"))
        # reused evidence must at least belong to this task; fresh runs must also postdate their launch
        problem = ("report belongs to a different task" if report.get("task") != t["task"] else None) if reuse else fresh(report, t, launched)
        if problem:
            rows.append({**row, "pass": False, "status": report.get("status"), "fails": [problem]})
            continue
        fails = score(t, report, out)
        rows.append({**row, "pass": not fails, "fails": fails, "status": report["status"], "elapsed_s": report["elapsed_s"],
                     "steps": report["steps"]["used"], "bodhan_inr": report["cost"]["total_counted_inr"], "cache_hits": report["cost"]["cache_hits"],
                     "paid_calls": report["cost"]["bodhan_calls"],
                     "llm_model": report["llm"]["model"],
                     "llm_tokens": report["cost"]["llm"]["prompt_tokens"] + report["cost"]["llm"]["completion_tokens"],
                     "latency_target_met": report["quality_targets"]["latency"]["met"], "latency_kind": report["quality_targets"]["latency"]["kind"],
                     "result": _shown(out / "result.md"), "trace": _shown(out / "trace.jsonl")})
    passed = sum(r["pass"] for r in rows)
    summary = {"passed": passed, "total": len(rows), "pass_rate": round(passed / max(len(rows), 1), 3), "scored_at": datetime.now(UTC).isoformat(timespec="seconds"),
               "code": code_version(), "checks": "structural only (status, files, kinds, languages, verification, answer text, budget); not a quality score",
               "tasks": rows}
    dest = Path(a.summary_dir).resolve() if a.summary_dir else base
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"# Suite: {passed}/{len(rows)} passed structural checks", "",
             f"Code: `{summary['code']['commit']}`{' (with local changes)' if summary['code']['src_modified'] else ''}. Scored {summary['scored_at']}.", "",
             "| Task | Evidence | Cache | Status | Pass | Time (s) | Steps | Bodhan ₹ | Paid calls | Cache hits | Latency target | Failed checks |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r['evidence']} | {r['cache_mode']} | {r.get('status')} | {'✅' if r['pass'] else '❌'} | {r.get('elapsed_s', '')} | "
                     f"{r.get('steps', '')} | {r.get('bodhan_inr', '')} | {r.get('paid_calls', '')} | {r.get('cache_hits', '')} | "
                     f"{r.get('latency_kind', '')} {'met' if r.get('latency_target_met') else 'missed'} | "
                     f"{'; '.join(r['fails'])} |")
    (dest / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    sys.exit(0 if passed == len(rows) else 1)


if __name__ == "__main__":
    main()
