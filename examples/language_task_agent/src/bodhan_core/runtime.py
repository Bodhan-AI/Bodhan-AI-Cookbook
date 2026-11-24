"""Local trust boundaries, cost reservations and an append-only run journal."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    def as_dict(self) -> dict:
        return {"ok": False, "error": {"code": self.code, "message": str(self)}}


def digest(value: Any) -> str:
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(value).hexdigest()


def decimal(value: str | float | Decimal) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ToolError("invalid_budget", "Budget or price is not a decimal number.") from exc
    if not result.is_finite() or result < 0:
        raise ToolError(
            "invalid_budget", "Budget and price values must be finite and non-negative."
        )
    return result


@dataclass
class Limits:
    max_cost: Decimal = Decimal("20")
    max_steps: int = 30
    call_timeout: float = 45.0
    run_timeout: float = 300.0

    def __post_init__(self):
        self.max_cost = decimal(self.max_cost)
        if self.max_steps < 1 or not all(
            math.isfinite(v) and v > 0 for v in (self.call_timeout, self.run_timeout)
        ):
            raise ToolError("invalid_budget", "Steps and timeouts must be positive and finite.")


class Run:
    def __init__(self, input_root: Path, output: Path, limits: Limits):
        self.input_root = input_root.resolve(strict=True)
        self.output = output.resolve()
        # Refuse reusing a run. This prevents stale artifacts from masquerading as new output.
        if self.output.exists() and any(self.output.iterdir()):
            raise ToolError("output_exists", "Choose an empty --out folder for a new run.")
        self.output.mkdir(parents=True, exist_ok=True)
        self.artifacts = self.output / "artifacts"
        self.artifacts.mkdir()
        self.work = self.output / ".work"
        self.work.mkdir(mode=0o700)
        self.limits = limits
        self.started = time.monotonic()
        self.elapsed_before = 0.0
        self.execution_window = None
        self.resume_count = 0
        self.operations = {}
        self.cost = Decimal(0)
        self.steps = 0
        self.refs: dict[str, Path] = {}
        self.provenance: dict[str, str] = {}
        self.checks: list[dict] = []
        self.assumptions: list[str] = []
        self.calls: list[dict] = []
        self.cache: dict[str, dict] = {}
        self.uncertain: set[str] = set()
        self.trace = self.output / "trace.jsonl"
        self.trace.touch()

    def elapsed(self):
        return self.elapsed_before + time.monotonic() - self.started

    def check_window(self, headroom=0):
        if (
            self.execution_window is not None
            and time.monotonic() - self.started + headroom >= self.execution_window
        ):
            raise ToolError(
                "execution_window",
                "Pause at a safe request boundary; cumulative task budgets are retained.",
            )

    def event(self, kind: str, **fields):
        record = {
            "kind": kind,
            "elapsed_seconds": round(self.elapsed(), 3),
            "cost_inr_reserved": str(self.cost),
            **fields,
        }
        with self.trace.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def remaining_time(self) -> float:
        left = self.limits.run_timeout - (self.elapsed())
        if left <= 0:
            raise ToolError(
                "time_budget", "Run time budget exhausted; partial artifacts are retained."
            )
        return left

    def step(self):
        self.remaining_time()
        self.check_window()
        if self.steps >= self.limits.max_steps:
            raise ToolError("step_budget", "Maximum agent/tool steps reached.")
        self.steps += 1

    def reserve(self, amount: Decimal, model: str) -> int:
        self.remaining_time()
        amount = decimal(amount)
        if self.cost + amount > self.limits.max_cost:
            raise ToolError("cost_budget", f"Insufficient remaining INR budget for {model}.")
        self.cost += amount
        self.calls.append({"model": model, "reserved_inr": str(amount), "status": "reserved"})
        return len(self.calls) - 1

    def settle(self, index: int, actual: Decimal | None, status: str):
        call = self.calls[index]
        reserved = Decimal(call["reserved_inr"])
        if actual is not None:
            actual = decimal(actual)
            if actual > reserved:
                # No more calls are safe if the vendor violates our upper bound.
                call.update(status="pricing_bound_breached", reported_inr=str(actual))
                self.cost += actual - reserved
                self.event("pricing_bound_breached", model=call["model"])
                raise ToolError(
                    "pricing_bound_breached", "Vendor usage exceeded the reserved bound; stop."
                )
            self.cost -= reserved - actual
            call["actual_inr"] = str(actual)
        call["status"] = status
        self.event("billing", **call)

    def register(self, path: Path, provenance: str) -> str:
        path = path.resolve(strict=True)
        if not (path.is_relative_to(self.input_root) or path.is_relative_to(self.output)):
            raise ToolError("unsafe_reference", "File is outside the configured roots.")
        ref = "file_" + digest(str(path))[:16]
        self.refs[ref] = path
        self.provenance[ref] = provenance
        return ref

    def resolve(self, ref: str) -> Path:
        if ref not in self.refs:
            raise ToolError(
                "missing_file", "Unknown reference. Call list_files or use a returned ref."
            )
        path = self.refs[ref].resolve(strict=True)
        if not (path.is_relative_to(self.input_root) or path.is_relative_to(self.output)):
            raise ToolError("unsafe_reference", "Reference escaped its root.")
        if path.stat().st_size > 100 * 1024 * 1024:
            raise ToolError("file_too_large", "File exceeds the local 100 MB safety limit.")
        return path

    def save_text(self, text: str, stem: str, provenance: str = "bodhan") -> str:
        path = self.artifacts / f"{stem}_{digest(text)[:12]}.md"
        path.write_text(text, encoding="utf-8")
        return self.register(path, provenance)

    def write_report(self, status: str, explanation: str, selected: list[str] | None = None):
        selected = selected or []
        artifacts = []
        for ref, path in self.refs.items():
            if path.is_relative_to(self.artifacts):
                artifacts.append(
                    {
                        "ref": ref,
                        "path": str(path.relative_to(self.output)),
                        "provenance": self.provenance[ref],
                        "selected": ref in selected,
                    }
                )
        report = {
            "status": status,
            "resume_count": self.resume_count,
            "lineage": getattr(self, "lineage", None),
            "execution_kind": "budget_carried_correction"
            if getattr(self, "lineage", None)
            else ("paused_resumed_autonomous" if self.resume_count else "single_process"),
            "steps": self.steps,
            "verification": self.checks,
            "total_cost_inr": str(self.cost),
            "cost_kind": "actual_where_known_else_reserved_upper_bound",
            "total_seconds": round(self.elapsed(), 3),
            "calls": self.calls,
            "artifacts": artifacts,
            "assumptions": self.assumptions,
            "explanation": explanation,
            "quality_targets": {
                "hidden_task_pass_rate": "not measured",
                "native_review": "not performed",
                "single_step_under_30_seconds": self.elapsed() < 30,
                "multi_step_under_300_seconds": self.elapsed() < 300,
            },
            "planner_model": os.environ.get("LLM_MODEL", "not configured"),
        }
        (self.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        lines = [f"# Task result: {status}", "", explanation, "", "## Assumptions"]
        lines += [f"- {x}" for x in self.assumptions] or ["- None recorded."]
        lines += ["", "## Artifacts (partial outputs may be unverified)"]
        lines += [f"- [{a['path']}]({a['path']})" for a in artifacts]
        lines += [
            "",
            "Generated speech is AI-generated. See report.json for checks and cost bounds.",
        ]
        (self.output / "result.md").write_text("\n".join(lines), encoding="utf-8")
        self.event("final", status=status, explanation=explanation)
        return report
