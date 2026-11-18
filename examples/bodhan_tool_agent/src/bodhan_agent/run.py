"""Run orchestration: preflight -> plan -> act -> verify -> report.

Scaffold: preflight, output layout, trace and report are wired; planning/execution are TODO.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from .trace import Trace


@dataclass
class RunConfig:
    task: str
    files: list[Path]
    out: Path
    max_steps: int = 30
    max_cost_inr: float = 20.0
    call_timeout_s: float = 120.0


def collect_inputs(paths: list[Path]) -> tuple[list[Path], list[str]]:
    """Expand directories; return (existing files, missing paths)."""
    found, missing = [], []
    for p in paths:
        if p.is_dir():
            found.extend(sorted(f for f in p.rglob("*") if f.is_file() and not f.name.startswith(".")))
        elif p.is_file():
            found.append(p)
        else:
            missing.append(str(p))
    return found, missing


def run_task(cfg: RunConfig) -> dict:
    started = time.monotonic()
    (cfg.out / "artifacts").mkdir(parents=True, exist_ok=True)
    trace = Trace(cfg.out / "trace.jsonl")
    trace.log("start", task=cfg.task, budgets={"max_steps": cfg.max_steps, "max_cost_inr": cfg.max_cost_inr})

    files, missing = collect_inputs(cfg.files)
    trace.log("preflight", files=[str(f) for f in files], missing=missing)

    if missing or not files:
        status = "impossible"
        answer = "Input files are missing: " + (", ".join(missing) if missing else "no files found") + "."
    else:
        # TODO: plan (LLM) -> feasibility check (code) -> act via MCP -> verify -> retry/stop.
        status = "not_implemented"
        answer = "Planning and execution are not implemented yet."

    trace.log("stop", status=status)
    report = {
        "status": status,
        "task": cfg.task,
        "steps": trace.step,
        "verification": [],
        "assumptions": [],
        "total_cost_inr": 0.0,
        "total_time_s": round(time.monotonic() - started, 2),
        "missed_targets": [],
    }
    (cfg.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (cfg.out / "result.md").write_text(
        f"# Result\n\n**Task:** {cfg.task}\n\n**Status:** {status}\n\n{answer}\n", encoding="utf-8"
    )
    return report
