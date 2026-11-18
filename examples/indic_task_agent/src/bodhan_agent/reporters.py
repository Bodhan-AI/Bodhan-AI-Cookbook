"""The four run files: result.md, report.json, trace.jsonl (written by Trace) and artifacts/."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_report(
    path: Path,
    *,
    task: str,
    status: str,
    started_at: str,
    duration_s: float,
    max_steps: int,
    max_cost_inr: float,
    cost_inr: float,
    reserved_inr: float,
    assumptions: list[dict[str, str]],
    records: list[Any],
    checks: list[dict[str, str]],
    artifacts: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    replans: int,
    cache_hits: int,
    notes: list[str],
    verification_inr: float = 0.0,
) -> dict[str, Any]:
    report = {
        "schema": 1,
        "task": task,
        "status": status,
        "started_at": started_at,
        "duration_s": round(duration_s, 1),
        "budget": {
            "max_steps": max_steps,
            "steps_used": len(records),
            "max_cost_inr": max_cost_inr,
            "cost_inr": round(cost_inr, 4),
            "reserved_inr": round(reserved_inr, 4),
            "verification_inr": round(verification_inr, 4),
        },
        "assumptions": assumptions,
        "steps": [
            {
                "id": r.id,
                "intent": r.intent,
                "tool": r.tool,
                "args": r.args,
                "ok": r.ok,
                "latency_ms": r.latency_ms,
                "cost_inr": r.cost_inr,
                "cache_hit": r.cache_hit,
                "result_summary": {k: v for k, v in r.result.items() if k not in ("blocks", "chunks")},
                **({"error": r.error} if r.error else {}),
            }
            for r in records
        ],
        "verification": checks,
        "artifacts": artifacts,
        "errors": errors,
        "replans": replans,
        "cache_hits": cache_hits,
        "quality_targets": {
            "completion": status == "completed",
            "verification_complete": all(c["result"] != "skipped" for c in checks),
            "no_waste": True,
            "within_budget": cost_inr <= max_cost_inr + 1e-9,
            "latency_ok": duration_s < 300,
            "notes": notes,
        },
    }
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def write_result_md(
    path: Path,
    *,
    task: str,
    status: str,
    understanding: str,
    records: list[Any],
    assumptions: list[dict[str, str]],
    checks: list[dict[str, str]],
    artifacts: list[dict[str, Any]],
    cost_inr: float,
    max_cost_inr: float,
    duration_s: float,
    errors: list[dict[str, Any]],
    notes: list[str],
    srt_note: bool = False,
) -> None:
    lines = [f"# {task}", "", f"**Status:** {status}", "", understanding or "", ""]
    if errors:
        lines += ["## Why this is not complete", ""]
        for err in errors:
            lines.append(f"- `{err.get('code')}`: {err.get('message')}" + (f" — {err['hint']}" if err.get("hint") else ""))
        lines.append("")
    lines += ["## What was done", ""]
    if not records:
        lines += ["Nothing was run; no model was called.", ""]
    for i, r in enumerate(records, 1):
        state = "ok" if r.ok else "failed"
        lines.append(f"{i}. **{r.intent}** — `{r.tool}`, {state}, Rs {r.cost_inr:.2f}, {r.latency_ms / 1000:.1f}s")
    lines.append("")
    lines += ["## Assumptions", ""]
    lines += [f"- {a['text']}" + (f" ({a['why']})" if a.get("why") else "") for a in assumptions] or ["- None."]
    lines.append("")
    lines += ["## Verification", ""]
    if checks:
        lines += ["| artifact | check | result | detail |", "|---|---|---|---|"]
        lines += [f"| `{c['artifact']}` | {c['check']} | {c['result']} | {c['detail']} |" for c in checks]
    else:
        lines.append("No artifact reached verification.")
    lines.append("")
    lines += ["## Artifacts", ""]
    for a in artifacts:
        label = a.get("kind", "file")
        note = " — AI-generated (Indic-Speak)" if label == "audio" else ""
        lines.append(f"- [{a['name']}](artifacts/{a['name']}) — {label}, {a['bytes']} bytes" + (f", {a['detail']}" if a.get("detail") else "") + note)
    if not artifacts:
        lines.append("None.")
    lines.append("")
    lines += ["## Cost and time", "", f"Rs {cost_inr:.2f} of the Rs {max_cost_inr:.2f} budget in {duration_s:.0f}s."]
    lines.append("")
    lines += ["## Limitations", ""]
    if srt_note:
        lines.append("- Subtitle timing is interpolated within each transcription window: the transcription API returns text without word timings.")
    lines += [f"- {n}" for n in notes] or ["- None beyond the model's own quality."]
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
