from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from bodhan_agent.schemas.report import StopReason
from bodhan_agent.schemas.trace import LlmTokens
from bodhan_agent.schemas.verify import Verdict


class LoopModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FinishProposal(LoopModel):
    answer_en: str
    assumptions: list[str] = Field(default_factory=list)
    deliverables: dict[str, str | None] = Field(
        default_factory=dict, description="Deliverable id to artifact id; None for an answer."
    )
    implicit: bool = Field(default=False, description="True when taken from a reply without a tool call.")


class LoopResult(LoopModel):
    stop_reason: StopReason
    explanation: str | None = None
    finish: FinishProposal | None = None
    deliverables: dict[str, str | None] = Field(
        default_factory=dict, description="Artifact chosen for each planned deliverable, None when missing."
    )
    verdicts: dict[str, Verdict] = Field(default_factory=dict)
    steps_used: int = Field(default=0, ge=0)
    llm_turns: int = Field(default=0, ge=0)
    llm_tokens: LlmTokens = Field(default_factory=LlmTokens)
    redactions: int = Field(default=0, ge=0)
    blocked_llm_outputs: int = Field(default=0, ge=0)
    injection_flags: int = Field(default=0, ge=0, description="Instruction-like lines found in tool output.")
    missed_targets: list[str] = Field(default_factory=list)
    verification_retry_used: bool = False
