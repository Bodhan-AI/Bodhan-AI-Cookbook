from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bodhan_agent.schemas.ledger import Producer
from bodhan_agent.schemas.plan import DeliverableFormat, DeliverableKind, StopReason
from bodhan_agent.schemas.trace import LlmTokens

RunStatus = Literal["completed", "partial", "failed", "impossible"]
DeliverableStatus = Literal["verified", "warning", "failed", "missing"]
CheckStatus = Literal["passed", "warning", "failed", "skipped"]
Target = Literal["task_completion", "honest_failure", "verification", "no_waste", "budget", "latency"]

ARTIFACTS_DIR = "artifacts"
USABLE_DELIVERABLE: frozenset[str] = frozenset({"verified", "warning"})
CAPABILITY_STOPS: frozenset[str] = frozenset(
    {"unsupported_language", "missing_input", "unsupported_input", "beyond_tools"}
)


def status_for(deliverables: list[DeliverableReport], stop_reason: StopReason) -> RunStatus:
    usable = sum(1 for item in deliverables if item.status in USABLE_DELIVERABLE)
    if deliverables and usable == len(deliverables):
        return "completed"
    if usable:
        return "partial"
    return "impossible" if stop_reason in CAPABILITY_STOPS else "failed"


class ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VerificationCheck(ReportModel):
    name: str = Field(min_length=1, description="Check performed, e.g. 'speech_word_recall' or 'srt_parses'.")
    status: CheckStatus = Field(
        description="'failed' for a required check that did not pass; 'warning' for an advisory problem.",
    )
    detail: str | None = None
    value: float | None = Field(default=None, allow_inf_nan=False)
    threshold: float | None = Field(default=None, allow_inf_nan=False)


class DeliverableReport(ReportModel):
    id: str
    kind: DeliverableKind
    language: str | None = None
    format: DeliverableFormat | None = None
    status: DeliverableStatus
    path: str | None = Field(default=None, description="File under artifacts/, relative to out/.")
    artifact_id: str | None = None
    producer: Producer | None = None
    tool: str | None = None
    label: str | None = Field(default=None, description="AI-generated disclosure for audio.")
    checks: list[VerificationCheck] = Field(default_factory=list)

    @field_validator("path")
    @classmethod
    def _check_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or len(path.parts) < 2 or path.parts[0] != ARTIFACTS_DIR:
            raise ValueError(f"deliverable path must be a file under {ARTIFACTS_DIR}/, relative to out/.")
        return value

    @model_validator(mode="after")
    def _check(self) -> DeliverableReport:
        if self.kind == "answer" and self.path is not None:
            raise ValueError(f"deliverable {self.id}: an answer lives in result.md and has no path.")
        if self.status == "missing" and self.path is not None:
            raise ValueError(f"deliverable {self.id}: a missing deliverable has no path.")
        if self.status in USABLE_DELIVERABLE and self.kind != "answer" and self.path is None:
            raise ValueError(f"deliverable {self.id}: a usable file deliverable needs a path.")
        if self.status in USABLE_DELIVERABLE and self.kind == "audio" and not self.label:
            raise ValueError(f"deliverable {self.id}: audio must carry the AI-generated label.")
        statuses = {check.status for check in self.checks}
        if "failed" in statuses and self.status != "failed":
            raise ValueError(f"deliverable {self.id}: a failed check makes the deliverable 'failed'.")
        if self.status == "verified" and "warning" in statuses:
            raise ValueError(f"deliverable {self.id}: a deliverable with warning checks is 'warning', not 'verified'.")
        if self.status == "warning" and "warning" not in statuses:
            raise ValueError(f"deliverable {self.id}: 'warning' needs at least one warning check.")
        return self


class Usage(ReportModel):
    used: float = Field(ge=0, allow_inf_nan=False)
    limit: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @property
    def exceeded(self) -> bool:
        return self.limit is not None and self.used > self.limit + 1e-9


class CountUsage(ReportModel):
    used: int = Field(ge=0)
    limit: int | None = Field(default=None, ge=0)

    @property
    def exceeded(self) -> bool:
        return self.limit is not None and self.used > self.limit


class CostSummary(ReportModel):
    bodhan_inr: float = Field(
        ge=0,
        allow_inf_nan=False,
        description="Settled Bodhan cost plus possible charges counted conservatively.",
    )
    reserved_unsettled_inr: float = Field(
        default=0.0,
        ge=0,
        allow_inf_nan=False,
        description="Reservations still open at the end of the run, not included in bodhan_inr.",
    )
    billing_uncertain: bool = False
    possibly_billed_failed_attempts: int = Field(default=0, ge=0)
    cache_hits: int = Field(default=0, ge=0)
    llm_tokens: LlmTokens = Field(default_factory=LlmTokens)
    llm_usage_complete: bool = Field(description="False when any LLM call did not report its token usage.")

    @property
    def exposure_inr(self) -> float:
        return self.bodhan_inr + self.reserved_unsettled_inr

    @model_validator(mode="after")
    def _check(self) -> CostSummary:
        if self.llm_usage_complete and self.llm_tokens.total is None:
            raise ValueError("llm_usage_complete needs both prompt and completion token counts.")
        return self


class Budgets(ReportModel):
    cost_inr: Usage
    steps: CountUsage
    llm_turns: CountUsage
    time_s: Usage


class MissedTarget(ReportModel):
    target: Target
    detail: str = Field(min_length=1)


class Compliance(ReportModel):
    indic_redactions: int = Field(default=0, ge=0, description="Indian-language passages hidden from the LLM.")
    blocked_llm_outputs: int = Field(default=0, ge=0, description="LLM outputs rejected for Indian-language text.")
    injection_flags: int = Field(default=0, ge=0, description="Instruction-like lines found in input content.")
    media_to_llm: int = Field(default=0, ge=0, description="Images or audio sent to the LLM.")
    non_bodhan_ml: int = Field(default=0, ge=0, description="Speech, translation or OCR done outside Bodhan.")
    uncertain_retries: int = Field(default=0, ge=0)


class RunReport(ReportModel):
    status: RunStatus
    stop_reason: StopReason
    explanation: str | None = Field(default=None, description="Why the run did not complete, in English.")
    task: str = Field(description="The task as given on the command line.")
    task_en: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    planned: list[str] = Field(
        default_factory=list,
        description="Ids of every deliverable in the plan; empty when the run stopped before planning.",
    )
    deliverables: list[DeliverableReport] = Field(default_factory=list)
    budgets: Budgets
    cost: CostSummary
    started_at: datetime
    finished_at: datetime
    missed_targets: list[MissedTarget] = Field(default_factory=list)
    compliance: Compliance = Field(default_factory=Compliance)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("started_at", "finished_at")
    @classmethod
    def _check_tz(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware.")
        return value

    @model_validator(mode="after")
    def _check(self) -> RunReport:
        ids = [item.id for item in self.deliverables]
        if len(ids) != len(set(ids)):
            raise ValueError("deliverable ids must be unique.")
        if set(ids) != set(self.planned) or len(self.planned) != len(set(self.planned)):
            raise ValueError("every planned deliverable must be reported exactly once, including missing ones.")
        expected = status_for(self.deliverables, self.stop_reason)
        if self.status != expected:
            raise ValueError(f"status '{self.status}' does not match the deliverables; expected '{expected}'.")
        if self.status != "completed" and not self.explanation:
            raise ValueError("a run that did not complete needs an explanation.")
        if abs(self.budgets.cost_inr.used - self.cost.exposure_inr) > 1e-6:
            raise ValueError("budgets.cost_inr.used must equal bodhan_inr plus reserved_unsettled_inr.")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at is before started_at.")
        return self

    def to_json(self) -> str:
        return self.model_dump_json(indent=2, exclude_none=True)
