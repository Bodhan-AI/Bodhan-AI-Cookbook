from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

BoundKind = Literal["exact", "upper", "assumed"]


class BudgetModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Quote(BudgetModel):
    tool: str
    amount_inr: float = Field(ge=0, allow_inf_nan=False, description="Reservation for one attempt per model request.")
    bound: BoundKind = Field(
        description=(
            "'exact' when the charge is known in advance, 'upper' when it cannot be exceeded without retries, "
            "'assumed' when a missing fact (length, pages, token ceiling) was filled with a conservative cap."
        ),
    )
    parts: dict[str, float] = Field(default_factory=dict, description="Cost per model, e.g. speak and transcribe.")
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> Quote:
        if any(value < 0 for value in self.parts.values()):
            raise ValueError("quote parts cannot be negative.")
        if self.parts and abs(sum(self.parts.values()) - self.amount_inr) > 1e-6:
            raise ValueError("quote parts must add up to amount_inr.")
        return self


class Reservation(BudgetModel):
    id: str = Field(min_length=1)
    tool: str
    amount_inr: float = Field(ge=0, allow_inf_nan=False)


class MinimumCost(BudgetModel):
    known_inr: float = Field(ge=0, allow_inf_nan=False, description="Cost the plan cannot avoid.")
    items: dict[str, float] = Field(default_factory=dict, description="Known cost per input or step.")
    unknown: list[str] = Field(default_factory=list, description="Costs that cannot be priced before work starts.")
