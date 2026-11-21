from __future__ import annotations

import re
from pathlib import PurePath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bodhan_agent.schemas.budget import MinimumCost
from bodhan_agent.schemas.trace import LlmTokens

DeliverableKind = Literal["audio", "subtitles", "markdown", "text", "answer"]
DeliverableFormat = Literal["mp3", "wav", "srt", "md", "txt"]
VoiceGender = Literal["female", "male"]
FeasibilityStatus = Literal["feasible", "infeasible"]
InfeasibleReason = Literal["unsupported_language", "missing_input", "unsupported_input", "beyond_tools"]
StopReason = Literal[
    "finished", "step_limit", "turn_limit", "budget_insufficient", "budget_exhausted", "deadline",
    "unsupported_language", "missing_input", "unsupported_input", "beyond_tools", "planner_error",
    "llm_unavailable", "internal_error",
]  # fmt: skip

KIND_FORMATS: dict[str, tuple[str, ...]] = {
    "audio": ("mp3", "wav"),
    "subtitles": ("srt",),
    "markdown": ("md", "txt"),
    "text": ("txt", "md"),
    "answer": (),
}
TEXT_DELIVERABLES: frozenset[str] = frozenset({"markdown", "text"})
TEXT_PRODUCERS: tuple[str, ...] = ("translate_text", "transcribe_audio", "ocr_document", "write")
KIND_PRODUCERS: dict[str, tuple[str, ...]] = {
    "audio": ("synthesize_speech",),
    "subtitles": ("make_subtitles",),
    "markdown": TEXT_PRODUCERS,
    "text": TEXT_PRODUCERS,
    "answer": (),
}

_LANGUAGE_RE = re.compile(r"^([A-Za-z]{2,3})(?:-([A-Za-z]{4}))?(?:-(?:[A-Za-z]{2}|\d{3}))?$")
_DELIVERABLE_ID_RE = re.compile(r"^D\d+$")
_STEP_ID_RE = re.compile(r"^S\d+$")


def normalize_language(code: str) -> str:
    match = _LANGUAGE_RE.match(code.strip())
    if match is None:
        raise ValueError(f"'{code}' is not a language code like 'ta', 'hi', 'fr', 'hi-IN' or 'ks-Deva'.")
    primary, script = match.groups()
    return primary.lower() if script is None else f"{primary.lower()}-{script.title()}"


def _base(code: str) -> str:
    return code.split("-", 1)[0].lower()


def _as_list(value: Any) -> Any:
    if isinstance(value, str):
        return [value] if value.strip() else []
    return value


def _find_cycle(graph: dict[str, list[str]]) -> str | None:
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(node: str) -> str | None:
        if node in done:
            return None
        if node in visiting:
            return node
        visiting.add(node)
        for dependency in graph[node]:
            found = visit(dependency)
            if found is not None:
                return found
        visiting.discard(node)
        done.add(node)
        return None

    for node in graph:
        found = visit(node)
        if found is not None:
            return found
    return None


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class StrictPlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class DeliverableConstraints(StrictPlanModel):
    duration_s: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        description="Target length of an audio deliverable in seconds, only when the task asks for one.",
    )
    voice_gender: VoiceGender | None = Field(default=None, description="Requested voice gender for audio.")
    keep_tables: bool = Field(default=False, description="True when tables in the source must survive in the output.")


class Deliverable(StrictPlanModel):
    id: str = Field(description="Deliverable id: D1, D2, ...")
    kind: DeliverableKind = Field(
        description=(
            "'audio' speech file, 'subtitles' SRT file, 'markdown' document whose structure such as tables must "
            "survive, 'text' plain text (.txt or .md), 'answer' an English answer written into result.md with no file."
        ),
    )
    language: str | None = Field(
        default=None,
        description=(
            "Language code of the deliverable's content, such as 'ta', 'en' or 'fr'. Give the language the user "
            "asked for even if you think it is unsupported; support is checked by code."
        ),
    )
    format: DeliverableFormat | None = Field(
        default=None,
        description="File format: audio mp3 or wav, subtitles srt, markdown md, text txt. Empty for 'answer'.",
    )
    produced_by: str | None = Field(
        default=None,
        description=(
            "Tool whose output file is delivered as is: translate_text, transcribe_audio or ocr_document for a "
            "translation, transcript or OCR text; write only for English you compose yourself, such as a summary. "
            "Audio is synthesize_speech, subtitles make_subtitles; none for 'answer'."
        ),
    )
    description: str = Field(min_length=1, description="What this deliverable contains, in English.")
    constraints: DeliverableConstraints = Field(default_factory=DeliverableConstraints)

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _DELIVERABLE_ID_RE.match(value):
            raise ValueError("deliverable id must look like D1, D2, ...")
        return value

    @field_validator("language")
    @classmethod
    def _check_language(cls, value: str | None) -> str | None:
        return None if value is None or not value.strip() else normalize_language(value)

    @model_validator(mode="after")
    def _check_format(self) -> Deliverable:
        allowed = KIND_FORMATS[self.kind]
        if self.format is None:
            self.format = allowed[0] if allowed else None
        elif self.format not in allowed:
            expected = ", ".join(allowed) if allowed else "no format"
            raise ValueError(f"deliverable {self.id}: kind '{self.kind}' takes {expected}, not '{self.format}'.")
        if self.kind != "audio" and (self.constraints.duration_s is not None or self.constraints.voice_gender):
            raise ValueError(f"deliverable {self.id}: duration_s and voice_gender apply only to audio.")
        producers = KIND_PRODUCERS[self.kind]
        if self.produced_by is None and len(producers) == 1:
            self.produced_by = producers[0]
        elif self.produced_by is not None and self.produced_by not in producers:
            expected = ", ".join(producers) if producers else "no produced_by"
            raise ValueError(f"deliverable {self.id}: kind '{self.kind}' takes {expected}, not '{self.produced_by}'.")
        if self.produced_by == "write" and self.language and _base(self.language) != "en":
            raise ValueError(
                f"deliverable {self.id}: write makes only English; '{self.language}' text comes from Bodhan."
            )
        return self

    def file_problem(self, kind: str, path: str) -> tuple[str, str] | None:
        name = PurePath(path).name
        suffix = PurePath(path).suffix.lower().lstrip(".")
        if self.kind in TEXT_DELIVERABLES:
            if kind not in TEXT_DELIVERABLES:
                return "kind", f"needs a text file, but it is {kind}"
            if suffix not in KIND_FORMATS[self.kind]:
                return "format", f"needs a .txt or .md file, not {name}"
            return None
        if kind != self.kind:
            return "kind", f"needs a {self.kind} file, but it is {kind}"
        if self.format and suffix != self.format:
            return "format", f"needs a .{self.format} file, not {name}"
        return None

    def producer_problem(self, tool: str | None) -> str | None:
        if self.produced_by in (None, "write") or tool != "write":
            return None
        return f"must be Bodhan's {self.produced_by} output, but it was written by the LLM with write"


class PlanStep(PlanModel):
    id: str = Field(description="Step id: S1, S2, ...")
    intent: str = Field(min_length=1, description="What this step achieves, in English.")
    why: str = Field(min_length=1, description="Why this step is needed and why this way, in one English sentence.")
    tool: str | None = Field(default=None, description="Tool expected for this step, if known.")
    condition: str | None = Field(
        default=None, description="When the step runs only in some cases, e.g. 'only if the text is not English'."
    )
    depends_on: list[str] = Field(default_factory=list, description="Ids of steps that must finish first.")

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _STEP_ID_RE.match(value):
            raise ValueError("step id must look like S1, S2, ...")
        return value


class Feasibility(PlanModel):
    status: FeasibilityStatus
    reason_code: InfeasibleReason | None = Field(default=None, description="Required when status is 'infeasible'.")
    explanation: str | None = Field(default=None, description="Why the task cannot be done, in English.")

    @model_validator(mode="after")
    def _check(self) -> Feasibility:
        if self.status == "infeasible" and (self.reason_code is None or not self.explanation):
            raise ValueError("an infeasible plan needs reason_code and explanation.")
        if self.status == "feasible":
            self.reason_code = None
        return self


class Risk(PlanModel):
    risk: str = Field(min_length=1, description="What could go wrong, in English.")
    mitigation: str = Field(min_length=1, description="What the agent will do about it, in English.")


class Plan(PlanModel):
    task_en: str = Field(min_length=1, description="The task restated in English.")
    goal: str = Field(min_length=1, description="What the user gets when the task is done, in one English sentence.")
    input_notes: list[str] = Field(
        default_factory=list,
        description="What is known or still unknown about the inputs and how it affects the approach.",
    )
    assumptions: list[str] = Field(
        default_factory=list,
        description=(
            "Choices made where the task is ambiguous, such as length, voice or output format. Only real "
            "ambiguities; ambiguity alone is not infeasibility."
        ),
    )
    deliverables: list[Deliverable] = Field(default_factory=list)
    steps: list[PlanStep] = Field(default_factory=list, description="Advisory order of work; not executed as is.")
    risks: list[Risk] = Field(default_factory=list, description="Likely problems and how they will be handled.")
    feasibility: Feasibility

    @field_validator("input_notes", "assumptions", mode="before")
    @classmethod
    def _check_lists(cls, value: Any) -> Any:
        return _as_list(value)

    @model_validator(mode="after")
    def _check(self) -> Plan:
        deliverable_ids = [item.id for item in self.deliverables]
        if len(deliverable_ids) != len(set(deliverable_ids)):
            raise ValueError("deliverable ids must be unique.")
        step_ids = [step.id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step ids must be unique.")
        known = set(step_ids)
        for step in self.steps:
            unknown = [ref for ref in step.depends_on if ref not in known or ref == step.id]
            if unknown:
                raise ValueError(f"step {step.id} depends on unknown or itself: {', '.join(unknown)}.")
        cycle = _find_cycle({step.id: step.depends_on for step in self.steps})
        if cycle is not None:
            raise ValueError(f"step dependencies form a cycle involving {cycle}.")
        if self.feasibility.status == "feasible" and not self.deliverables:
            raise ValueError("a feasible plan needs at least one deliverable.")
        return self

    def deliverable(self, deliverable_id: str) -> Deliverable | None:
        return next((item for item in self.deliverables if item.id == deliverable_id), None)


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
