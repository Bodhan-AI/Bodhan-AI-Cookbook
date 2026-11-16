"""Final outputs of a run: out/artifacts/, out/result.md and out/report.json.

Everything here is written by code from the trace and the files on disk. The
only planner-written text is the English answer and assumptions; previews of
Indian-language outputs are copied verbatim from the files Bodhan tools wrote.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from bodhan_agent.verify import AUDIO_SUFFIXES, Ledger

EXIT_CODES = {"completed": 0, "partial": 2, "impossible": 2, "failed": 1}
TEXT_PREVIEW_SUFFIXES = frozenset({".md", ".txt", ".srt", ".vtt", ".csv"})
_PREVIEW_CHARS = 700
SINGLE_STEP_LATENCY_S = 30.0
MULTI_STEP_LATENCY_S = 300.0
PAID_TOOLS = frozenset({"transcribe_audio", "translate_text", "synthesize_speech", "ocr_document",
                        "verify_speech", "verify_translation"})


@dataclass
class ArtifactInfo:
    name: str
    rel_path: str
    source_path: str
    kind: str
    verified: bool | None  # None: no check applies to this kind of file
    verification: dict[str, Any] | None = None


@dataclass
class RunOutcome:
    status: str
    task: str
    answer: str
    stop_reason: str
    artifacts: list[ArtifactInfo] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    plans: list[dict[str, Any]] = field(default_factory=list)
    verification_failures: list[str] = field(default_factory=list)
    injection_warnings: list[str] = field(default_factory=list)
    inputs: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return EXIT_CODES.get(self.status, 1)


def clean_name(name: str) -> str:
    stem, dot, suffix = Path(name).name.rpartition(".")
    if not dot:
        stem, suffix = suffix, ""
    stem = re.sub(r"[^\w.-]+", "_", stem, flags=re.UNICODE).strip("._") or "artifact"
    suffix = re.sub(r"[^\w]+", "", suffix)
    return f"{stem[:80]}.{suffix}" if suffix else stem[:80]


def _unique(dest_dir: Path, name: str, taken: set[str]) -> str:
    candidate = name
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while candidate.lower() in taken or (dest_dir / candidate).exists():
        candidate = f"{stem}_{n}{suffix}"
        n += 1
    taken.add(candidate.lower())
    return candidate


def copy_artifacts(paths: list[str], ledger: Ledger, out_dir: Path) -> list[ArtifactInfo]:
    dest_dir = out_dir / "artifacts"
    dest_dir.mkdir(parents=True, exist_ok=True)
    taken: set[str] = set()
    infos: list[ArtifactInfo] = []
    for src in paths:
        source = Path(src)
        if not source.is_file():
            continue
        name = _unique(dest_dir, clean_name(source.name), taken)
        shutil.copy2(source, dest_dir / name)
        prov = ledger.get(src)
        kind = prov.kind if prov else ("audio" if source.suffix.lower() in AUDIO_SUFFIXES else "input")
        req = ledger.requirement_for(src)
        record = ledger.records.get(req.key) if req else None
        infos.append(ArtifactInfo(
            name=name,
            rel_path=f"artifacts/{name}",
            source_path=str(source),
            kind=kind,
            verified=(record.passed if record else False) if req else None,
            verification=record.as_dict() if record else None,
        ))
    return infos


def _preview(path: Path) -> str | None:
    if path.suffix.lower() not in TEXT_PREVIEW_SUFFIXES:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    text = text.strip()
    if len(text) > _PREVIEW_CHARS:
        text = text[:_PREVIEW_CHARS].rstrip() + "\n..."
    return text


def _tool_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] in ("tool", "verify") and e.get("tool")]


def quality_targets(outcome: RunOutcome, events: list[dict[str, Any]], elapsed_s: float,
                    spent_inr: float, max_cost_inr: float) -> dict[str, Any]:
    tool_events = _tool_events(events)
    paid_kinds = {e["tool"] for e in tool_events if e["tool"] in PAID_TOOLS and e["tool"] not in
                  ("verify_speech", "verify_translation")}
    single = len(paid_kinds) <= 1
    limit = SINGLE_STEP_LATENCY_S if single else MULTI_STEP_LATENCY_S
    seen: dict[str, int] = {}
    for e in tool_events:
        if e["tool"] in PAID_TOOLS and e.get("cost_inr", 0) > 0 and e.get("ok", True):
            key = e["tool"] + json.dumps(e.get("arguments"), sort_keys=True, ensure_ascii=False)
            seen[key] = seen.get(key, 0) + 1
    duplicates = sum(n - 1 for n in seen.values() if n > 1)
    needing = [a for a in outcome.artifacts if a.verified is not None]
    unverified = [a.name for a in needing if not a.verified]

    def target(met: bool, detail: str) -> dict[str, Any]:
        return {"met": met, "detail": detail}

    return {
        "latency": target(elapsed_s <= limit,
                          f"{elapsed_s:.1f}s against {limit:.0f}s ({'single' if single else 'multi'}-step task)"
                          + ("" if elapsed_s <= limit else "; time is dominated by model calls and retries")),
        "verification": target(not unverified,
                               "all generated audio/translations passed checks" if not unverified
                               else f"not verified: {', '.join(unverified)}"),
        "budget": target(spent_inr <= max_cost_inr + 1e-9, f"spent Rs {spent_inr:.4f} of Rs {max_cost_inr:.2f}"),
        "no_waste": target(duplicates == 0, f"{duplicates} repeated paid call(s) with identical arguments"),
        "honest_failure": target(outcome.status != "impossible" or not outcome.artifacts,
                                 "impossible tasks produce no artifacts"),
    }


def write_report_json(out_dir: Path, outcome: RunOutcome, *, events: list[dict[str, Any]], steps: int,
                      max_steps: int, max_cost_inr: float, spent_inr: float, exhausted: str | None,
                      elapsed_s: float, llm_usage: dict[str, Any], verification: list[dict[str, Any]],
                      cost_by_tool: dict[str, float]) -> dict[str, Any]:
    tool_events = _tool_events(events)
    report = {
        "status": outcome.status,
        "exit_code": outcome.exit_code,
        "stop_reason": outcome.stop_reason,
        "task": outcome.task,
        "answer": outcome.answer,
        "steps": steps,
        "tool_calls": len(tool_events),
        "tool_calls_by_name": _count_by(tool_events),
        "verification": verification,
        "verification_failures": outcome.verification_failures,
        "total_cost_inr": round(spent_inr, 4),
        "cost_by_tool_inr": {k: round(v, 4) for k, v in cost_by_tool.items()},
        "llm": llm_usage,
        "elapsed_s": round(elapsed_s, 2),
        "artifacts": [asdict(a) for a in outcome.artifacts],
        "assumptions": outcome.assumptions,
        "plan": outcome.plans[-1] if outcome.plans else None,
        "injection_warnings": outcome.injection_warnings,
        "inputs": outcome.inputs,
        "notes": outcome.notes,
        "budget": {"max_steps": max_steps, "max_cost_inr": max_cost_inr, "exhausted": exhausted},
        "quality_targets": quality_targets(outcome, events, elapsed_s, spent_inr, max_cost_inr),
    }
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _count_by(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for e in events:
        counts[e["tool"]] = counts.get(e["tool"], 0) + 1
    return counts


def _md_list(items: list[str], empty: str = "None.") -> str:
    return "\n".join(f"- {i}" for i in items) if items else empty


def write_result_md(out_dir: Path, outcome: RunOutcome, report: dict[str, Any]) -> None:
    lines: list[str] = [
        "# Task result",
        "",
        f"**Status:** {outcome.status}  ",
        f"**Task:** {outcome.task}  ",
        f"**Cost:** Rs {report['total_cost_inr']:.4f} (Bodhan models) · **Time:** {report['elapsed_s']:.1f}s · "
        f"**Steps:** {report['steps']} · **Tool calls:** {report['tool_calls']}",
        "",
        "## Answer",
        "",
        outcome.answer.strip() or "(no answer)",
        "",
    ]
    if any(a.kind == "audio" for a in outcome.artifacts):
        lines += ["> **AI-generated audio:** the audio in this run was synthesized by Bodhan Indic-Speak from a "
                  "fixed voice library. It is not a recording of a real person.", ""]
    if outcome.verification_failures:
        lines += ["## Verification problems", "", _md_list(outcome.verification_failures), ""]
    lines += ["## Artifacts", ""]
    if outcome.artifacts:
        for a in outcome.artifacts:
            state = {True: "verified", False: "NOT verified", None: "no check needed"}[a.verified]
            lines.append(f"- [{a.name}]({a.rel_path}) ({a.kind}, {state})")
        lines.append("")
        for a in outcome.artifacts:
            preview = _preview(out_dir / a.rel_path)
            if preview:
                lines += [f"### Preview: {a.name}", "", "```text", preview, "```", ""]
    else:
        lines += ["No artifacts were produced.", ""]
    lines += ["## Assumptions", "", _md_list(outcome.assumptions), ""]
    lines += ["## What was done", ""]
    plan = outcome.plans[-1] if outcome.plans else None
    if plan:
        lines += [f"Plan: {plan.get('goal', '')}", "", _md_list([str(s) for s in plan.get("steps", [])]), ""]
    lines += ["Tool calls (from trace.jsonl):", "", _md_list(_tool_lines(report)), ""]
    lines += ["## Verification", "", _md_list(_verification_lines(report["verification"]),
                                               "No generated audio or translations required checks."), ""]
    if outcome.injection_warnings:
        lines += ["## Prompt-injection warnings", "",
                  "Input content contained instruction-like text. It was treated as data and not followed.", "",
                  _md_list(outcome.injection_warnings), ""]
    budget = report["budget"]
    lines += ["## Budget", "",
              f"- Steps: {report['steps']} of {budget['max_steps']}",
              f"- Cost: Rs {report['total_cost_inr']:.4f} of Rs {budget['max_cost_inr']:.2f}",
              f"- Exhausted: {budget['exhausted'] or 'no'}", ""]
    if outcome.notes:
        lines += ["## Notes", "", _md_list(outcome.notes), ""]
    lines += ["Files: [trace.jsonl](trace.jsonl) · [report.json](report.json)", ""]
    (out_dir / "result.md").write_text("\n".join(lines), encoding="utf-8")


def _tool_lines(report: dict[str, Any]) -> list[str]:
    return [f"{name} x{n}" for name, n in sorted(report["tool_calls_by_name"].items())]


def _verification_lines(records: list[dict[str, Any]]) -> list[str]:
    out = []
    for r in records:
        issues = f" - {'; '.join(r['issues'][:3])}" if r.get("issues") else ""
        out.append(f"{Path(r['target']).name}: {'passed' if r['passed'] else 'FAILED'} ({r['by']}){issues}")
    return out
