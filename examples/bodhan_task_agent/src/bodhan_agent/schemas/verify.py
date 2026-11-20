from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bodhan_agent.schemas.report import DeliverableStatus, VerificationCheck

StepOutcome = Literal["ok", "warning", "failed"]


class VerifyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StepVerdict(VerifyModel):
    status: StepOutcome = Field(description="Status for the artifacts a tool call produced; never 'pending'.")
    warnings: list[str] = Field(default_factory=list)
    tool_checks: dict[str, Any] = Field(
        default_factory=dict,
        description="Facts from the tool's own checks, copied as-is into Artifact.tool_checks.",
    )


class Verdict(VerifyModel):
    status: DeliverableStatus
    checks: list[VerificationCheck] = Field(default_factory=list)
    label: str | None = Field(default=None, description="AI-generated disclosure for audio deliverables.")
