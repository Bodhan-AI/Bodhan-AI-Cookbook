"""Writes result.md and report.json from the final run state. Plain code: nothing here comes from the LLM except
the English `summary` and `assumptions` it submitted via finish()."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

STATUSES = ("completed", "partial", "impossible", "failed", "budget_exhausted")
TEXT_SUFFIXES = {".md", ".txt", ".srt"}


@dataclass
class ArtifactRecord:
    path: str  # relative to the out dir
    kind: str  # audio | text | srt | json
    ai_generated: bool = False
    verified: bool | None = None  # None = no verification applies / not attempted
    verification: str = ""


@dataclass
class RunState:
    task: str
    files: list[str]
    status: str = "failed"
    summary: str = ""
    stop_reason: str = ""
    assumptions: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)
    artifacts: dict[str, ArtifactRecord] = field(default_factory=dict)
    steps: int = 0
    tool_calls: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # honesty notes added by code (downgrades, unverified outputs)
    manifest: list[dict[str, Any]] = field(default_factory=list)
    llm_model: str = ""
    max_steps: int = 0
    max_cost_inr: float | None = None
    llm_tokens: dict[str, int] = field(default_factory=lambda: {"prompt": 0, "completion": 0})
    warned_unverified: bool = False


def targets_missed(state: RunState, elapsed: float) -> list[str]:
    missed = []
    unverified = [a.path for a in state.artifacts.values() if a.kind in ("audio",) and a.verified is not True]
    if unverified:
        missed.append(f"Verification target: not verified: {', '.join(unverified)}")
    if state.tool_calls <= 1 and elapsed > 30:
        missed.append(f"Latency target: single-step task took {elapsed:.0f}s (> 30s)")
    if state.tool_calls > 1 and elapsed > 300:
        missed.append(f"Latency target: multi-step task took {elapsed:.0f}s (> 300s)")
    return missed


def write_report(out: Path, state: RunState, cost: dict[str, Any], elapsed: float) -> dict[str, Any]:
    report = {
        "status": state.status,
        "stop_reason": state.stop_reason,
        "task": state.task,
        "steps": state.steps,
        "tool_calls": state.tool_calls,
        "limits": {"max_steps": state.max_steps, "max_cost_inr": state.max_cost_inr},
        "elapsed_s": round(elapsed, 1),
        "cost": cost,
        "verification": [
            {"artifact": a.path, "kind": a.kind, "verified": a.verified, "detail": a.verification}
            for a in state.artifacts.values()
        ],
        "deliverables": state.deliverables,
        "artifacts": [a.path for a in state.artifacts.values()],
        "assumptions": state.assumptions,
        "notes": state.notes,
        "errors": state.errors,
        "targets_missed": targets_missed(state, elapsed),
        "llm": {"model": state.llm_model, "tokens": state.llm_tokens},
        "inputs": state.manifest,
    }
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def write_result_md(out: Path, state: RunState, cost: dict[str, Any], elapsed: float) -> None:
    L: list[str] = []
    headline = {
        "completed": "Completed",
        "partial": "PARTIALLY completed",
        "impossible": "Cannot be done",
        "failed": "FAILED",
        "budget_exhausted": "Stopped: budget exhausted",
    }.get(state.status, state.status)
    L += [f"# Result: {headline}", "", f"**Task:** {state.task}", ""]
    if state.stop_reason:
        L += [f"**Stop reason:** {state.stop_reason}", ""]
    L += ["## Summary", "", state.summary.strip() or "_No summary was produced._", ""]
    if state.notes:
        L += ["## Honesty notes", ""] + [f"- {n}" for n in state.notes] + [""]
    L += ["## Assumptions", ""] + ([f"- {a}" for a in state.assumptions] or ["- None recorded."]) + [""]

    shown = [state.artifacts[p] for p in state.deliverables if p in state.artifacts] or list(state.artifacts.values())
    L += ["## Artifacts", ""]
    if not shown:
        L += ["_No files were produced._", ""]
    for a in shown:
        tag = " (AI-generated audio; not a recording of a real person)" if a.ai_generated else ""
        ver = "" if a.verified is None else (" - verified" if a.verified else " - NOT verified")
        L.append(f"- [{a.path}]({a.path}){tag}{ver}" + (f": {a.verification}" if a.verification else ""))
        if Path(a.path).suffix.lower() in TEXT_SUFFIXES and (out / a.path).exists():
            body = (out / a.path).read_text(encoding="utf-8", errors="replace")
            clip = body[:3000] + ("\n...[truncated; see file]" if len(body) > 3000 else "")
            L += ["", "  ```text"] + ["  " + ln for ln in clip.splitlines()] + ["  ```"]
    L += ["", "## Cost and budget", ""]
    L += [f"- Bodhan model spend: Rs {cost['total_inr']:.3f}" + (f" of Rs {cost['max_inr']:.2f} cap" if cost.get("max_inr") is not None else "")]
    L += [f"- By model: {cost['by_model_inr']}", f"- Cache hits (free): {cost['cache_hits']}", f"- Steps: {state.steps}/{state.max_steps}, tool calls: {state.tool_calls}, time: {elapsed:.0f}s", ""]
    (out / "result.md").write_text("\n".join(L), encoding="utf-8")
