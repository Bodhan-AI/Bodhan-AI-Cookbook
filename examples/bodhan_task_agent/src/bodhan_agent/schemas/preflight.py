from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

IssueCode = Literal[
    "missing_input",
    "empty_input",
    "unreadable_input",
    "unsupported_input",
    "too_many_files",
    "skipped_hidden",
    "skipped_link",
    "tool_missing",
]


class PreflightModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InputIssue(PreflightModel):
    path: str
    code: IssueCode
    message: str = Field(min_length=1)


class PreflightResult(PreflightModel):
    task: str = Field(description="The task exactly as given on the command line.")
    task_for_llm: str = Field(
        description="The task with literal text replaced by placeholders; never contains Indian-language script.",
    )
    needs_translation: bool = Field(
        description="True when the instruction itself is in an Indian language and Bodhan must translate it first.",
    )
    instruction_id: str | None = Field(
        default=None,
        description="L0, the original task saved as a file, when needs_translation is true.",
    )
    literal_ids: list[str] = Field(default_factory=list, description="L1.. literal text extracted from the task.")
    input_ids: list[str] = Field(default_factory=list, description="f1.. input files registered in the ledger.")
    issues: list[InputIssue] = Field(default_factory=list)
    missing_tools: list[str] = Field(default_factory=list, description="System tools not found, e.g. ffprobe.")

    @model_validator(mode="after")
    def _check(self) -> PreflightResult:
        if self.needs_translation != (self.instruction_id is not None):
            raise ValueError("instruction_id is set exactly when needs_translation is true.")
        if self.instruction_id is not None and self.instruction_id != "L0":
            raise ValueError("the original task is always saved as L0.")
        if "L0" in self.literal_ids:
            raise ValueError("L0 is the original task, not an extracted literal.")
        return self
