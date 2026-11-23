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
    would_be_cost_inr: float = 0.0
    cost_charged_inr: float = 0.0
    llm_latency_ms: int = 0
    tool_latency_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        charged = self.cost_charged_inr if self.cost_charged_inr else self.cost_inr
        would_be = self.would_be_cost_inr if self.would_be_cost_inr else (0.0 if self.cached else charged)
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
            "cost_inr": round(charged, 4),
            "cached": self.cached,
            "would_be_cost_inr": round(would_be, 4),
            "cost_charged_inr": round(charged, 4),
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
    # Distinct statuses: success, partial, max_steps_reached, budget_exceeded (money only), timeout, impossible_task, failed
    status: str
    task: str
    steps_taken: int
    total_cost_inr: float
    total_time_s: float
    steps: list[dict[str, Any]] = field(default_factory=list)
    total_would_be_cost_inr: float = 0.0
    assumptions: list[str] = field(default_factory=list)
    verification_results: list[VerificationResult] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    audio_language: str | None = None
    target_language: str | None = None
    language_requested: str | None = None
    detected_script: str | None = None
    windows_count: int | None = None
    language_ambiguity: str | None = None
    warnings: list[str] = field(default_factory=list)
    known_limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "status": self.status,
            "task": self.task,
            "steps_taken": self.steps_taken,
            "total_cost_inr": round(self.total_cost_inr, 4),
            "total_would_be_cost_inr": round(self.total_would_be_cost_inr, 4),
            "total_time_s": round(self.total_time_s, 2),
            "steps": self.steps,
            "assumptions": self.assumptions,
            "verification_results": [v.to_dict() for v in self.verification_results],
            "artifacts": self.artifacts,
            "failures": self.failures,
            "warnings": self.warnings,
            "audio_language": self.audio_language,
            "target_language": self.target_language,
            "language_requested": self.language_requested,
            "detected_script": self.detected_script,
            "windows_count": self.windows_count,
            "known_limitations": self.known_limitations,
            "known limitations": self.known_limitations,
        }
        if self.language_ambiguity:
            d["language_ambiguity"] = self.language_ambiguity
        return d
