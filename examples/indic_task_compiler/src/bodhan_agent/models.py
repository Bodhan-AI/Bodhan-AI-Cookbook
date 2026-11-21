"""Typed Pydantic models for TaskSpec, probes, compiled plans, contracts, traces, and reports."""

from __future__ import annotations

from typing import Any, Literal

from bodhan_mcp.schemas import FileFact
from pydantic import BaseModel, Field

DeliverableKind = Literal["audio", "subtitles", "markdown", "text", "answer"]
RunStatus = Literal["success", "partial", "failed", "infeasible"]


class DeliverableConstraints(BaseModel):
    duration_s: float | None = None
    target_words: int | None = None
    voice: str | None = None
    voice_gender: Literal["female", "male"] | None = None
    style: str | None = None
    keep_tables: bool = False
    format: str | None = None  # e.g. "mp3", "wav", "srt", "vtt", "md", "txt"
    script: Literal["native", "roman", "codemix"] = "native"
    filter_language: str | None = None


class DeliverableSpec(BaseModel):
    id: str
    kind: DeliverableKind
    language_raw: str | None = None
    language: str | None = None  # Resolved deterministically from rules.py
    language_supported: bool = True
    output_filename: str | None = None
    description: str = ""
    constraints: DeliverableConstraints = Field(default_factory=DeliverableConstraints)


class ProbeRequest(BaseModel):
    kind: Literal["detect_language"] = "detect_language"
    path: str


class TaskSpec(BaseModel):
    summary: str = ""
    deliverables: list[DeliverableSpec] = Field(default_factory=list)
    source_language_raw: str | None = None
    source_language: str | None = None
    inputs_used: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    needs_probe: list[ProbeRequest] = Field(default_factory=list)
    infeasible_reason: str | None = None


class ProbeFact(BaseModel):
    path: str
    language: str | None = None
    confidence: float = 0.0
    ambiguous: bool = False
    method: str = ""
    cost_inr: float = 0.0
    error: str | None = None


class ForeachSpec(BaseModel):
    over: Literal["probe_facts", "file_facts"] = "probe_facts"
    filter: dict[str, Any] = Field(default_factory=dict)
    as_var: str = Field(default="item", alias="as")

    model_config = {"populate_by_name": True}


class PlanStep(BaseModel):
    id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    foreach: ForeachSpec | None = None
    deliverable_id: str | None = None
    rationale: str = ""


class RawPlan(BaseModel):
    steps: list[PlanStep] = Field(default_factory=list)
    final_answer_template: str = ""
    assumptions: list[str] = Field(default_factory=list)


class CompiledStep(BaseModel):
    id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    deliverable_id: str | None = None
    estimated_cost_inr: float = 0.0
    rationale: str = ""


class CompiledPlan(BaseModel):
    waves: list[list[CompiledStep]] = Field(default_factory=list)
    steps_by_id: dict[str, CompiledStep] = Field(default_factory=dict)
    estimated_cost_ceiling_inr: float = 0.0
    final_answer_template: str = ""
    assumptions: list[str] = Field(default_factory=list)


class FeasibilityResult(BaseModel):
    feasible: bool
    reason: str | None = None
    cost_floor_inr: float = 0.0
    checks: list[str] = Field(default_factory=list)


class ContractCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class DeliverableVerification(BaseModel):
    deliverable_id: str
    kind: DeliverableKind
    artifact_path: str | None = None
    passed: bool
    checks: list[ContractCheck] = Field(default_factory=list)


class ProvenanceRecord(BaseModel):
    handle: str
    path: str
    source: str  # e.g. "bodhan:indic-translate", "bodhan:indic-ocr", "q_llm:en", "input_file", "deterministic:subtitles"
    model: str | None = None
    language: str | None = None
    sha256: str
    parent_handles: list[str] = Field(default_factory=list)


class TraceEvent(BaseModel):
    step_index: int
    timestamp: str
    epoch: str  # intake | epoch_a | gate | probe | epoch_b | compile | execute | verify | epoch_c | finalize
    plan_excerpt: str = ""
    tool: str
    args_sanitized: dict[str, Any] = Field(default_factory=dict)
    result_meta: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    latency_ms: int = 0
    cost_inr: float = 0.0
    cache_hit: bool = False


class RunReport(BaseModel):
    status: RunStatus
    task: str
    steps_used: int
    max_steps: int
    total_cost_inr: float
    max_cost_inr: float
    cost_by_model: dict[str, float] = Field(default_factory=dict)
    cache_hits: int = 0
    total_time_s: float = 0.0
    file_facts_count: int = 0
    assumptions: list[str] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    verification: list[DeliverableVerification] = Field(default_factory=list)
    quality_targets: dict[str, dict[str, Any]] = Field(default_factory=dict)
    reason: str | None = None
    camel_boundary_enforced: bool = True
    input_facts: list[FileFact] = Field(default_factory=list)
