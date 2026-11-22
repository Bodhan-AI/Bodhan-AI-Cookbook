"""Data models for task agent state, traces, and reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class StepTrace:
    step: int
    timestamp: str
    plan: str
    tool: str
    arguments: dict[str, Any]
    result_summary: str
    latency_ms: int
    cost_inr: float
    cached: bool = False
    llm_latency_ms: int = 0
    tool_latency_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "timestamp": self.timestamp,
            "plan": self.plan,
            "tool": self.tool,
            "arguments": self.arguments,
            "result_summary": self.result_summary,
            "latency_ms": self.latency_ms,
            "llm_latency_ms": self.llm_latency_ms,
            "tool_latency_ms": self.tool_latency_ms,
            "cost_inr": round(self.cost_inr, 4),
            "cached": self.cached,
        }


@dataclass
class VerificationResult:
    artifact: str
    verification_type: str  # "back_transcription" | "back_translation" | "source_fidelity" | "format_check"
    passed: bool
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact,
            "verification_type": self.verification_type,
            "passed": self.passed,
            "notes": self.notes,
        }


@dataclass
class AgentReport:
    # Distinct statuses: success, max_steps_reached, budget_exceeded (money only), timeout, impossible_task, failed
    status: str
    task: str
    steps_taken: int
    total_cost_inr: float
    total_time_s: float
    assumptions: list[str] = field(default_factory=list)
    verification_results: list[VerificationResult] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "task": self.task,
            "steps_taken": self.steps_taken,
            "total_cost_inr": round(self.total_cost_inr, 4),
            "total_time_s": round(self.total_time_s, 2),
            "assumptions": self.assumptions,
            "verification_results": [v.to_dict() for v in self.verification_results],
            "artifacts": self.artifacts,
            "failures": self.failures,
        }
