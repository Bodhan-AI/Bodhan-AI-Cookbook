"""What a run leaves behind: trace.jsonl, report.json and result.md.

trace.jsonl has one JSON line per event (plan, LLM call, tool call, guard decision, verification, final).
The status is computed here, in code, from what actually happened; the LLM's own claims never set it.
Indian-language text in result.md is embedded verbatim from the artifact files.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from bodhan_mcp.client import redact

from . import checks

if TYPE_CHECKING:
    from .graph import Run


class Tracer:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._fh = path.open("w", encoding="utf-8")
        self._seq = 0
        self._t0 = time.monotonic()
        self.events: list[dict] = []

    def elapsed(self) -> float:
        return time.monotonic() - self._t0

    def event(self, type_: str, **fields) -> dict:
        self._seq += 1
        record = {"seq": self._seq, "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
                  "elapsed_ms": int(self.elapsed() * 1000), "type": type_, **fields}
        line = redact(json.dumps(record, ensure_ascii=False, default=str))
        self._fh.write(line + "\n")
        self._fh.flush()
        self.events.append(json.loads(line))
        return record

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()


def summarize_result(data: dict, limit: int = 240) -> dict:
    """Compact view of a tool result for the trace (full results live in the files)."""
    if not isinstance(data, dict):
        return {"value": str(data)[:limit]}
    meta = data.get("meta") or {}
    keep = {k: meta[k] for k in list(meta)[:14] if not isinstance(meta[k], (list, dict)) or k in ("detected",)}
    return {"result_path": data.get("result_path"), "preview": (data.get("preview") or "")[:limit],
            "language": data.get("language"), "meta": keep, "warnings": data.get("warnings") or []}


EMBED_LIMIT = 8000
STOP_LABELS = {"max_steps": "the step budget (--max-steps) ran out",
               "max_cost": "the cost budget (--max-cost-inr) ran out",
               "max_seconds": "the time budget ran out",
               "llm_error": "the LLM could not be reached or rejected the request"}


def compute_status(run: Run, state: dict) -> tuple[str, str]:
    if state.get("refusal"):
        return "refused", " ".join(state["refusal"]["reasons"])
    stop = state.get("stop_reason")
    has_artifacts = bool(run.artifacts)
    if stop == "llm_error":
        return ("partial" if has_artifacts else "failed"), f"{STOP_LABELS[stop]}: {state.get('stop_detail', '')}"
    if stop in STOP_LABELS:
        return "budget_exhausted", f"Stopped because {STOP_LABELS[stop]}."
    finish = state.get("finish")
    if not finish:
        return ("partial" if has_artifacts else "failed"), "The agent stopped without finishing."
    if finish.get("outcome") == "cannot_complete":
        return ("refused" if not has_artifacts else "partial"), "The agent reported that the task cannot be completed."
    expected = [o.get("kind") for o in (state.get("spec") or {}).get("expected_outputs", [])]
    have = {a["kind"] for a in run.artifacts}
    missing = [k for k in expected if k != "answer" and k not in have and not (k == "text" and "markdown" in have)]
    if missing:
        return "partial", f"Expected output(s) not produced: {', '.join(missing)}."
    final_failed = [a for a in run.artifacts if (a.get("verification") or {}).get("passed") is False]
    if final_failed:
        return "completed_with_warnings", "Some outputs did not pass verification; see the verification section."
    return "completed", "All requested outputs were produced; checks passed where applicable."


def _quality_targets(run: Run, status: str, elapsed: float) -> dict:
    # Latest result per file, covering delivered files and intermediate translations alike.
    latest: dict[str, dict] = {}
    for v in run.verifications:
        latest[v["file"]] = v
    checked = [v for v in latest.values() if v.get("passed") is not None]
    passed = [v for v in checked if v["passed"] is True]
    must_check = ("synthesize_speech", "translate_text", "transliterate_text", "make_subtitles")
    unchecked = [a for a in run.artifacts
                 if a.get("tool") in must_check and (a.get("verification") or {}).get("passed") is None]
    verification_met = len(passed) == len(checked) and not unchecked
    single_step = run.steps <= 1
    latency_target = 30 if single_step else 300
    return {
        "task_completion": {"met": status in ("completed", "completed_with_warnings"), "status": status},
        "honest_failure": {"met": status not in ("refused", "failed") or not run.artifacts,
                           "why": "no artifacts are produced for refused/failed tasks"},
        "verification": {"met": verification_met, "checked": len(checked), "passed": len(passed),
                         "why": "" if verification_met else
                         "some generated audio/translations failed or skipped verification (see verification)"},
        "no_waste": {"met": True, "reused_duplicate_calls": run.reused_calls,
                     "why": "identical tool calls are reused; the server cache never resends the same input"},
        "budget": {"met": run.cost_inr <= run.cfg.max_cost_inr + 1e-9, "cost_inr": round(run.cost_inr, 4),
                   "max_cost_inr": run.cfg.max_cost_inr},
        "latency": {"met": elapsed <= latency_target, "elapsed_s": round(elapsed, 1), "target_s": latency_target,
                    "why": "" if elapsed <= latency_target else "speech synthesis/transcription runs ~1 s per audio second"},
    }


def _embed(art: dict) -> str | None:
    path = Path(art["path"])
    if art["kind"] not in ("text", "markdown", "subtitles") or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if len(text) > EMBED_LIMIT:
        text = text[:EMBED_LIMIT] + "\n…(truncated; see the file)"
    fence = "markdown" if art["kind"] == "markdown" else ("srt" if art["kind"] == "subtitles" else "text")
    return f"```{fence}\n{text}\n```"


def write_outputs(run: Run, state: dict) -> dict:
    cfg = run.cfg
    status, reason = compute_status(run, state)
    elapsed = run.elapsed()
    finish = state.get("finish") or {}
    answer_raw = str(finish.get("answer") or "").strip()
    answer, removed = checks.strip_indic(answer_raw)
    if removed:
        run.warnings.append(f"Removed {removed} Indian-language passage(s) the LLM wrote in its answer.")
    for item in finish.get("assumptions") or []:
        if isinstance(item, str) and item and not checks.contains_indic(item) and item not in run.assumptions:
            run.assumptions.append(item)
    spec = state.get("spec") or {}

    # --- result.md -----------------------------------------------------------------------
    lines = ["# Task result", "",
             f"**Status:** `{status}` — {reason}", "",
             f"**Cost:** Rs {run.cost_inr:.4f} of Rs {cfg.max_cost_inr:.2f} · **Time:** {elapsed:.0f} s · "
             f"**Tool calls:** {run.steps}/{cfg.max_steps} · **LLM calls:** {run.llm_calls}", "",
             "## Task", "", "> " + cfg.task.replace("\n", "\n> "), ""]
    if state.get("refusal"):
        lines += ["## Why this task was not done", ""] + [f"- {r}" for r in state["refusal"]["reasons"]] + \
                 ["", "No output files were produced.", ""]
    if answer:
        lines += ["## Answer", "", answer, ""]
    if run.artifacts:
        lines += ["## Outputs", "", "| File | What | Language | Verification |", "|---|---|---|---|"]
        for art in run.artifacts:
            ver = art.get("verification") or {}
            mark = {True: "passed", False: "FAILED", None: "not checked"}[ver.get("passed")]
            if ver.get("skipped"):
                mark = f"skipped ({ver['skipped']})"
            lines.append(f"| [{art['file']}](artifacts/{art['file']}) | {art['description'] or art['kind']} | "
                         f"{art.get('language') or '-'} | {mark} |")
        lines.append("")
        audio = [a for a in run.artifacts if a["kind"] == "audio" and a.get("tool") == "synthesize_speech"]
        if audio:
            lines += ["All audio files above are **AI-generated speech** (Bodhan Indic-Speak); the files also carry "
                      "this label in their metadata.", ""]
        for art in run.artifacts:
            block = _embed(art)
            if block:
                source = "written by the agent (English)" if art.get("tool") == "llm" else \
                    "produced by Bodhan models (not written by the LLM)"
                lines += [f"### {art['file']}", "", f"_{source}_", "", block, ""]
    if run.assumptions:
        lines += ["## Assumptions", ""] + [f"- {a}" for a in run.assumptions] + [""]
    failed_checks = [v for v in run.verifications if v.get("passed") is False]
    if run.verifications:
        lines += ["## Verification", ""]
        for v in run.verifications:
            state_txt = {True: "passed", False: "failed", None: "skipped"}[v.get("passed")]
            detail = "; ".join(v.get("problems") or []) or v.get("skipped") or ""
            extra = f" (similarity {v['similarity']:.0%})" if "similarity" in v else ""
            tag = " (intermediate)" if v.get("intermediate") else ""
            lines.append(f"- `{v['file']}`{tag}: {v['check']} {state_txt}{extra}{' — ' + detail if detail else ''}")
        lines.append("")
    steps = [e for e in run.tracer.events if e["type"] == "tool_call"]
    if steps:
        lines += ["## What was done", ""]
        for e in steps:
            status_txt = "ok" if e["ok"] else f"error {((e.get('error') or {}).get('code'))}"
            cost = f"Rs {e.get('cost_inr', 0):.4f}" + (" (cached)" if e.get("cached") else "")
            lines.append(f"{e['step']}. `{e['tool']}` — {status_txt}, {cost}, {e['latency_ms']} ms")
        lines.append("")
    notes = list(run.warnings)
    if run.injection_flags:
        notes.append("Input content contained instruction-like text (" + "; ".join(sorted(set(run.injection_flags))[:4]) +
                     "). It was treated as data and not followed.")
    if notes:
        lines += ["## Notes", ""] + [f"- {n}" for n in notes] + [""]
    lines += ["Trace: [trace.jsonl](trace.jsonl) · Report: [report.json](report.json)", ""]
    (cfg.out_dir / "result.md").write_text("\n".join(lines), encoding="utf-8")

    # --- report.json ---------------------------------------------------------------------
    by_tool: dict[str, float] = {}
    for e in steps:
        by_tool[e["tool"]] = round(by_tool.get(e["tool"], 0.0) + float(e.get("cost_inr") or 0), 5)
    report = {
        "task": cfg.task,
        "status": status,
        "status_reason": reason,
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "elapsed_s": round(elapsed, 2),
        "answer": answer,
        "refusal": state.get("refusal"),
        "stop_reason": state.get("stop_reason"),
        "plan": {k: spec.get(k) for k in ("intent", "output_languages", "expected_outputs", "steps")} if spec else None,
        "llm": {"model": getattr(getattr(run.llm, "settings", None), "model", None), "calls": run.llm_calls,
                "prompt_tokens": run.prompt_tokens, "completion_tokens": run.completion_tokens,
                "note": "LLM token cost is not included in cost_inr (provider pricing varies)"},
        "budgets": {"max_steps": cfg.max_steps, "steps_used": run.steps, "max_cost_inr": cfg.max_cost_inr,
                    "max_seconds": cfg.max_seconds, "call_timeout_s": cfg.call_timeout},
        "cost": {"total_inr": round(run.cost_inr, 5), "verification_inr": round(run.verification_cost_inr, 5),
                 "by_tool_inr": by_tool, "min_estimate_inr": run.min_cost_estimate,
                 "note": "Bodhan model spend only"},
        "steps": [{"step": e["step"], "tool": e["tool"], "ok": e["ok"], "cost_inr": e.get("cost_inr"),
                   "cached": e.get("cached"), "reused": e.get("reused"), "latency_ms": e["latency_ms"]} for e in steps],
        "artifacts": [{"file": a["file"], "kind": a["kind"], "language": a.get("language"), "tool": a.get("tool"),
                       "description": a["description"], "bytes": Path(a["path"]).stat().st_size,
                       "verification": a.get("verification")} for a in run.artifacts],
        "verification": run.verifications,
        "assumptions": run.assumptions,
        "warnings": run.warnings,
        "injection_flags": sorted(set(run.injection_flags)),
        "missing_inputs": run.missing_inputs,
        "quality_targets": _quality_targets(run, status, elapsed),
        "failed_checks": len(failed_checks),
    }
    (cfg.out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    run.tracer.event("final", status=status, reason=reason, cost_inr=round(run.cost_inr, 5), steps=run.steps,
                     llm_calls=run.llm_calls, artifacts=[a["file"] for a in run.artifacts], elapsed_s=round(elapsed, 1))
    return report
