from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bodhan_agent.schemas.budget import MinimumCost
from bodhan_agent.schemas.plan import Plan
from bodhan_agent.schemas.report import StopReason
from bodhan_agent.schemas.trace import LlmTokens


class PlannerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GateResult(PlannerModel):
    feasible: bool
    stop_reason: StopReason | None = Field(default=None, description="Why the run cannot go on; set when not feasible.")
    explanation: str | None = None
    assumptions: list[str] = Field(default_factory=list, description="Notes the gate adds to the plan's assumptions.")
    minimum_cost: MinimumCost | None = None

    @model_validator(mode="after")
    def _check(self) -> GateResult:
        if self.feasible == (self.stop_reason is not None):
            raise ValueError("stop_reason is set exactly when the task is not feasible.")
        if not self.feasible and not self.explanation:
            raise ValueError("an infeasible gate result needs an explanation.")
        return self


class PlanOutcome(PlannerModel):
    task_en: str = Field(description="The English task the plan was made from.")
    plan: Plan | None = None
    gate: GateResult
    instruction_en_id: str | None = Field(default=None, description="Bodhan's English translation of L0, if any.")
    llm_turns: int = Field(default=0, ge=0)
    llm_tokens: LlmTokens = Field(default_factory=LlmTokens)
    blocked_llm_outputs: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check(self) -> PlanOutcome:
        if self.gate.feasible and self.plan is None:
            raise ValueError("a feasible outcome needs a plan.")
        return self
