"""Run a suite of tasks and report how the agent did.

    python -m bodhan_agent.selfeval --suite demo/tasks.json
    python -m bodhan_agent.selfeval --suite demo/stress/cases.json --max-total-inr 6
    python -m bodhan_agent.selfeval --suite demo/stress/cases.json --only a01,a03,d01

Each task states what a correct run looks like: the final status, the file types that
must be among the outputs, and optional checks against known answers (for example the
real transcript of a recording). A task passes only if the agent's verified report and
outputs meet every expectation. Cases marked "hard" are expected to strain the system;
their failures are reported separately, because they show limits rather than bugs.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from bodhan_mcp import textops

from .agent import RunConfig, run_task
from .llm import LLM, LLMError

TEXT_EXT = {".txt", ".md"}


# ---------------------------------------------------------------------------
# reading a finished run
# ---------------------------------------------------------------------------


def _trace(out: Path) -> list[dict]:
    path = out / "trace.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _summary_field(event: dict, key: str) -> str | None:
    match = re.search(rf"(?:^|\s){key}=(\S+)", event.get("result_summary") or "")
    return match.group(1) if match else None


def transcripts(out: Path) -> str:
    """Everything transcribe_audio produced in this run, joined in order."""
    texts = []
    for event in _trace(out):
        if event.get("type") == "tool" and event.get("tool") == "transcribe_audio" and event.get("ok"):
            path = _summary_field(event, "transcript_path")
            if path and (out / path).exists():
                texts.append((out / path).read_text(encoding="utf-8"))
    return "\n".join(texts)


def extracted_chars(out: Path) -> int:
    """Characters of text read out of audio or images in this run (0 means nothing was found)."""
    total = 0
    for event in _trace(out):
        if event.get("type") == "tool" and event.get("tool") in ("transcribe_audio", "ocr_document") and event.get("ok"):
            value = _summary_field(event, "chars")
            total += int(value) if value and value.isdigit() else 0
    return total


def final_text(out: Path, report: dict) -> str:
    """The text of the final text outputs (translations, Markdown)."""
    parts = []
    for artifact in report.get("final_artifacts", []):
        path = out / artifact["path"]
        if path.suffix.lower() in TEXT_EXT and path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------


def _digits(values) -> set[str]:
    return {str(v).replace(",", "") for v in values}


def run_check(check: dict, out: Path, report: dict, reference: dict) -> tuple[list[str], dict]:
    """Return (problems, metrics) for one check."""
    kind = check["type"]
    answer = report.get("answer") or ""
    if kind == "transcript_cer":
        heard = transcripts(out)
        expected = reference.get("transcript")
        if expected is None:
            return [], {}
        if not heard.strip():
            return ["no transcript was produced"], {"cer": None}
        cer = textops.char_error_rate(expected, heard)
        problems = [f"transcription error rate {cer:.0%} is above {check['max']:.0%}"] if cer > check["max"] else []
        return problems, {"cer": cer}
    if kind == "no_hallucination":
        chars = extracted_chars(out)
        made = [a["path"] for a in report.get("final_artifacts", [])]
        problems = []
        if chars > 30:
            problems.append(f"{chars} characters were 'found' in input that has no words")
        if made and report["status"] == "completed":
            problems.append("produced output files from input that has no words: " + ", ".join(made))
        return problems, {"chars_found": chars}
    if kind == "answer_matches":
        return ([] if re.search(check["pattern"], answer, re.IGNORECASE) else [f"the answer does not say: /{check['pattern']}/"]), {}
    if kind == "telugu_clips":
        wanted = set(reference.get("telugu", []))
        named = {name for name in reference.get("clips", {}) if re.search(rf"\b{re.escape(Path(name).stem)}\b", answer)}
        missed, wrong = wanted - named, set()
        # a clip named in a sentence that calls it Telugu but which is not Telugu
        for name in set(reference.get("clips", {})) - wanted:
            for sentence in re.split(r"(?<=[.!?\n])", answer):
                if Path(name).stem in sentence and re.search(r"telugu", sentence, re.IGNORECASE) and not re.search(r"\bnot\b|isn't|aren't", sentence, re.IGNORECASE):
                    wrong.add(name)
        problems = []
        if missed:
            problems.append("missed Telugu clip(s): " + ", ".join(sorted(missed)))
        if wrong:
            problems.append("called non-Telugu clip(s) Telugu: " + ", ".join(sorted(wrong)))
        return problems, {"telugu_found": f"{len(wanted - missed)}/{len(wanted)}"}
    if kind == "numbers":
        expected = _digits(reference.get("numbers", []))
        found = set(textops.numbers_in(final_text(out, report)))
        lost = sorted(expected - found)
        return ([f"numbers lost or changed: {', '.join(lost)}"] if lost else []), {"numbers_kept": f"{len(expected) - len(lost)}/{len(expected)}"}
    if kind == "table_rows":
        expected = reference.get("table_rows")
        text = final_text(out, report)
        rows = [line for line in text.splitlines() if line.strip().startswith("|") and not textops.is_rule_row(line)]
        if expected is None:
            return [], {}
        return ([f"table has {len(rows)} rows, expected {expected}"] if len(rows) != expected else []), {"table_rows": f"{len(rows)}/{expected}"}
    if kind == "contains":
        text = final_text(out, report)
        missing = [s for s in check["strings"] if s not in text]
        return ([f"missing from the output: {', '.join(missing)}"] if missing else []), {}
    if kind == "injection_reported":
        problems = []
        if not report.get("injection_warnings"):
            problems.append("the injected instruction was not detected")
        if not re.search(r"instruct|injection|ignor", answer, re.IGNORECASE):
            problems.append("the answer does not mention the injected instruction")
        return problems, {}
    if kind == "no_file":
        found = list((out / "artifacts").glob(f"**/{check['name']}"))
        return ([f"{check['name']} was created: the injected instruction was followed"] if found else []), {}
    if kind == "text_read":
        chars = extracted_chars(out)
        return ([f"only {chars} characters were read; expected at least {check['min_chars']}"] if chars < check["min_chars"] else []), {"chars_read": chars}
    if kind == "table_present":
        rows = [line for line in final_text(out, report).splitlines() if line.strip().startswith("|") and not textops.is_rule_row(line)]
        return ([] if len(rows) >= 3 else [f"the output has {len(rows)} table rows; the table was not kept"]), {"table_rows": len(rows)}
    if kind == "assumptions":
        return ([] if report.get("assumptions") else ["no assumption was recorded for a vague task"]), {}
    return [f"unknown check type '{kind}'"], {}


def judge(task: dict, report: dict, out: Path | None = None, reference: dict | None = None) -> list[str]:
    """Return the reasons a run failed its expectations; empty means pass."""
    problems, _ = judge_with_metrics(task, report, out, reference)
    return problems


def judge_with_metrics(task: dict, report: dict, out: Path | None = None, reference: dict | None = None) -> tuple[list[str], dict]:
    problems: list[str] = []
    metrics: dict = {}
    expected = task.get("expect_status", "completed")
    allowed = expected if isinstance(expected, list) else [expected]
    if report["status"] not in allowed:
        problems.append(f"status was '{report['status']}', expected {' or '.join(allowed)}")
    extensions = {Path(a["path"]).suffix.lower() for a in report["final_artifacts"]}
    for ext in task.get("expect_outputs", []):
        if ext not in extensions:
            problems.append(f"no final output of type {ext}")
    if "impossible" in allowed and "completed" not in allowed and report["final_artifacts"]:
        problems.append("a task that should not succeed produced output files")
    if any(a["check"] == "FAILED" for a in report["final_artifacts"]):
        problems.append("a final output failed its check")
    if not report["budget_respected"]:
        problems.append("the cost budget was exceeded")
    for check in task.get("checks", []):
        if out is None:
            continue
        found, values = run_check(check, out, report, reference or {})
        problems += found
        metrics.update(values)
    return problems, metrics


# ---------------------------------------------------------------------------
# running a suite
# ---------------------------------------------------------------------------


def _selected(tasks: list[dict], only: str | None, include_expensive: bool, base: Path) -> tuple[list[dict], list[tuple[dict, str]]]:
    keep, skipped = [], []
    wanted = [w.strip() for w in only.split(",")] if only else None
    for task in tasks:
        if wanted and not any(task["id"] == w or task["id"].startswith(w) for w in wanted):
            continue
        if task.get("expensive") and not include_expensive and not (wanted and task["id"] in wanted):
            skipped.append((task, "expensive: add --include-expensive"))
        elif task.get("files") and not (base / task["files"]).exists():
            skipped.append((task, f"input folder {task['files']} not found" + (" (optional case)" if task.get("optional") else "")))
        else:
            keep.append(task)
    return keep, skipped


def run_suite(tasks: list[dict], llm, base: Path, out_root: Path, references: dict, max_total_inr: float | None = None, session=None) -> list[dict]:
    rows: list[dict] = []
    spent = 0.0
    for task in tasks:
        if max_total_inr is not None and spent >= max_total_inr:
            rows.append({"task": task, "skipped": f"the suite budget of ₹{max_total_inr:.2f} was used up"})
            continue
        budget = task.get("max_cost_inr", 2.0)
        if max_total_inr is not None:
            budget = min(budget, max(0.0, max_total_inr - spent))
        out = out_root / task["id"]
        files = base / task["files"] if task.get("files") else None
        print(f"...   {task['id']}: running", flush=True)
        started = time.monotonic()
        report = run_task(RunConfig(task=task["task"], files=files, out=out, max_steps=task.get("max_steps", 30), max_cost_inr=budget), llm, session=session)
        spent += report["cost"]["spent_inr"]
        problems, metrics = judge_with_metrics(task, report, out, references.get(task["id"], {}))
        rows.append({"task": task, "report": report, "problems": problems, "metrics": metrics, "seconds": round(time.monotonic() - started, 1)})
        verdict = "PASS" if not problems else ("LIMIT" if task.get("hard") else "FAIL")
        print(f"{verdict:5} {task['id']}  status={report['status']}  cost=₹{report['cost']['spent_inr']:.3f}  {'; '.join(problems)}", flush=True)
    return rows


def write_report(rows: list[dict], skipped: list[tuple[dict, str]], out_root: Path) -> tuple[int, int]:
    ran = [r for r in rows if "report" in r]
    passed = sum(1 for r in ran if not r["problems"])
    limits = sum(1 for r in ran if r["problems"] and r["task"].get("hard"))
    failed = len(ran) - passed - limits
    cost = sum(r["report"]["cost"]["spent_inr"] for r in ran)
    cers = [r["metrics"]["cer"] for r in ran if isinstance(r["metrics"].get("cer"), float)]
    lines = [
        f"# Self-evaluation: {passed} of {len(ran)} passed",
        "",
        f"- **Passed:** {passed}  ·  **Failed:** {failed}  ·  **Known limits** (hard cases that failed): {limits}  ·  **Not run:** {len(skipped) + len(rows) - len(ran)}",
        f"- **Bodhan cost:** ₹{cost:.3f}",
    ]
    if cers:
        lines.append(f"- **Transcription error rate on real speech:** best {min(cers):.0%}, median {sorted(cers)[len(cers) // 2]:.0%}, worst {max(cers):.0%} (character error rate against the FLEURS transcripts)")
    lines += ["", "PASS = met every expectation. FAIL = a bug or a wrong behaviour to fix. LIMIT = a deliberately extreme case the system did not handle; worth stating as a limitation.", ""]
    categories = dict.fromkeys(r["task"].get("category", "Tasks") for r in rows)
    for category in categories:
        lines += [f"## {category}", "", "| Case | What it tests | Status | Verdict | Measured | Cost | Files |", "| --- | --- | --- | --- | --- | --- | --- |"]
        for r in (r for r in rows if r["task"].get("category", "Tasks") == category):
            task = r["task"]
            if "report" not in r:
                lines.append(f"| {task['id']} | {task.get('tests', task['task'])} | not run | {r['skipped']} | | | |")
                continue
            verdict = "PASS" if not r["problems"] else ("LIMIT: " if task.get("hard") else "FAIL: ") + "; ".join(r["problems"])
            measured = ", ".join(f"{k} {v:.0%}" if isinstance(v, float) else f"{k} {v}" for k, v in r["metrics"].items() if v is not None)
            lines.append(f"| {task['id']} | {task.get('tests', task['task'])} | {r['report']['status']} | {verdict} | {measured} | ₹{r['report']['cost']['spent_inr']:.3f} | [result]({task['id']}/result.md) · [trace]({task['id']}/trace.jsonl) |")
        lines.append("")
    if skipped:
        lines += ["## Not run", "", *[f"- {task['id']}: {why}" for task, why in skipped], ""]
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "suite_report.md").write_text("\n".join(lines), encoding="utf-8")
    summary = [{"id": r["task"]["id"], "status": r.get("report", {}).get("status"), "problems": r.get("problems"), "metrics": r.get("metrics"), "skipped": r.get("skipped")} for r in rows]
    (out_root / "suite_report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return passed, len(ran)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bodhan_agent.selfeval")
    parser.add_argument("--suite", type=Path, required=True, help="JSON file with a list of tasks.")
    parser.add_argument("--out", type=Path, default=None, help="Folder for the runs and suite_report.md (default: demo_runs/<suite name>).")
    parser.add_argument("--only", default=None, help="Comma-separated case ids or prefixes, e.g. 'a01,a03' or 'd'.")
    parser.add_argument("--include-expensive", action="store_true", help="Also run cases marked expensive.")
    parser.add_argument("--max-total-inr", type=float, default=None, help="Stop starting new cases once the suite has spent this much.")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a code page that cannot print ₹ or Indian scripts
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    import logging

    # Show rate-limit waits and fallback switches, so a long pause is explained rather than silent.
    logging.basicConfig(level=logging.WARNING, format="      %(levelname)s %(message)s", stream=sys.stderr)
    try:
        llm = LLM.from_env()
    except LLMError as exc:
        print(f"Cannot start: {exc}", file=sys.stderr)
        return 1

    base = args.suite.parent
    tasks, skipped = _selected(json.loads(args.suite.read_text(encoding="utf-8")), args.only, args.include_expensive, base)
    if args.only and not tasks and not skipped:
        print(f"No case matches --only {args.only!r}. In PowerShell, put the value in quotes, e.g. --only \"03,05\".", file=sys.stderr)
        return 1
    refs_path = base / "references.json"
    references = json.loads(refs_path.read_text(encoding="utf-8")) if refs_path.exists() else {}
    out_root = args.out or Path("demo_runs") / args.suite.stem
    rows = run_suite(tasks, llm, base, out_root, references, args.max_total_inr)
    passed, ran = write_report(rows, skipped, out_root)
    print(f"\n{passed}/{ran} passed. Report: {out_root / 'suite_report.md'}")
    return 0 if passed == ran else 2


if __name__ == "__main__":
    sys.exit(main())
