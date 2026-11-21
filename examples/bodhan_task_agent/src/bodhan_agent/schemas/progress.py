from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ProgressStatus = Literal["pending", "in_progress", "done", "failed", "skipped"]


class ProgressModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StepProgress(ProgressModel):
    id: str
    intent: str
    why: str
    tool: str | None = None
    condition: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    status: ProgressStatus = "pending"
    calls: list[int] = Field(default_factory=list, description="Trace seq of each tool call made for this step.")
    outputs: list[str] = Field(default_factory=list, description="Ledger ids the step produced.")
    note: str | None = Field(default=None, description="Latest error or why the step was skipped.")


class Deviation(ProgressModel):
    seq: int = Field(description="Trace seq of the call that was not in the plan.")
    tool: str
    reason: str | None = Field(default=None, description="The LLM's stated reason for the call.")
    outcome: str


class DeliverableView(ProgressModel):
    id: str
    kind: str
    language: str | None = None
    format: str | None = None
    description: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    checks: list[str] = Field(default_factory=list, description="What code verifies before the task ends.")
    state: str = "not started"


class Estimate(ProgressModel):
    known_cost_inr: float = Field(ge=0, allow_inf_nan=False, description="Cost the plan cannot avoid.")
    unknown: list[str] = Field(default_factory=list, description="Costs that depend on earlier steps.")
    budget_inr: float = Field(ge=0, allow_inf_nan=False)


class PlanView(ProgressModel):
    revision: int = Field(ge=0, description="Increases every time the view changes.")
    task_en: str
    goal: str
    input_facts: list[str] = Field(default_factory=list, description="Facts about the inputs, found by code.")
    input_notes: list[str] = Field(default_factory=list, description="The planner's notes about the inputs.")
    assumptions: list[str] = Field(default_factory=list)
    deliverables: list[DeliverableView] = Field(default_factory=list)
    steps: list[StepProgress] = Field(default_factory=list)
    risks: list[dict[str, str]] = Field(default_factory=list)
    estimate: Estimate | None = None
    deviations: list[Deviation] = Field(default_factory=list)
    finished: bool = False
