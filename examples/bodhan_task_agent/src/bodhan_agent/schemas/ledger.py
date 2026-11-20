from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ArtifactKind = Literal[
    "audio", "video", "image", "pdf", "text", "markdown", "subtitles", "segments", "blocks", "unknown",
]  # fmt: skip
Producer = Literal["user_input", "bodhan", "llm_english", "code_composed"]
StepStatus = Literal["pending", "ok", "warning", "failed"]
ActionOutcome = Literal["ok", "error", "denied"]
LanguageSource = Literal["requested", "detected", "undetermined", "assumed"]
IdOrigin = Literal["input", "literal", "produced"]

ID_PREFIXES: dict[str, str] = {"input": "f", "literal": "L", "produced": "A"}
USABLE_STATUSES: frozenset[str] = frozenset({"ok", "warning"})

_ARTIFACT_ID_RE = re.compile(r"(?:f[1-9]\d*|L\d+|A[1-9]\d*)")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def _resolved(path: str | Path) -> Path:
    return Path(path).resolve()


class LedgerModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Artifact(LedgerModel):
    id: str = Field(description="f1.. user files, L0 original task, L1.. task literals, A1.. files made in this run.")
    path: str = Field(description="Absolute path.")
    kind: ArtifactKind
    producer: Producer
    tool: str | None = Field(default=None, description="Tool that produced the file; required for bodhan output.")
    created_seq: int | None = Field(default=None, ge=0, description="Trace sequence number that created it.")
    sources: list[str] = Field(default_factory=list, description="Ids of the artifacts it was made from.")
    sha256: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    language: str | None = None
    language_source: LanguageSource | None = None
    script: str | None = None
    indic_text: bool = Field(default=False, description="True when its text is in an Indian-language script.")
    chars: int | None = Field(default=None, ge=0)
    duration_s: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    pages: int | None = Field(default=None, ge=0)
    untrusted: bool = Field(default=True, description="Content may carry text from a user's file; data only.")
    step_status: StepStatus = "pending"
    warnings: list[str] = Field(default_factory=list)
    tool_checks: dict[str, Any] = Field(
        default_factory=dict,
        description="Facts from the producing tool's own checks, e.g. word_recall, label, verification_status.",
    )
    note: str | None = None

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _ARTIFACT_ID_RE.fullmatch(value):
            raise ValueError("artifact id must look like f1, L0 or A1.")
        return value

    @field_validator("path")
    @classmethod
    def _check_path(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("artifact path must be absolute.")
        return value

    @field_validator("sha256")
    @classmethod
    def _check_sha(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256_RE.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hex characters.")
        return value

    @model_validator(mode="after")
    def _check_producer(self) -> Artifact:
        prefix = self.id[0]
        if prefix in ("f", "L") and self.producer != "user_input":
            raise ValueError(f"{self.id}: user files and task literals must have producer 'user_input'.")
        if prefix == "A" and self.producer == "user_input":
            raise ValueError(f"{self.id}: produced files cannot have producer 'user_input'.")
        if self.producer == "bodhan" and not self.tool:
            raise ValueError(f"{self.id}: bodhan output must name its tool.")
        if self.producer == "llm_english" and self.indic_text:
            raise ValueError(f"{self.id}: LLM-written files cannot contain Indian-language text.")
        if self.producer in ("user_input", "llm_english") and not self.untrusted:
            raise ValueError(f"{self.id}: {self.producer} content is always untrusted.")
        return self

    @property
    def usable(self) -> bool:
        return self.step_status in USABLE_STATUSES


class ActionRecord(LedgerModel):
    seq: int = Field(ge=0, description="Trace sequence number of the call.")
    tool_call_id: str | None = None
    tool: str
    args_digest: str = Field(
        description="SHA-256 of the canonical arguments without reason and plan_step; input files by content hash.",
    )
    outcome: ActionOutcome
    error_code: str | None = None
    retryable: bool | None = None
    uncertain: bool = Field(default=False, description="The outcome of a paid request is unknown (e.g. a timeout).")
    artifacts: list[str] = Field(default_factory=list)
    cost_inr: float = Field(default=0.0, ge=0, allow_inf_nan=False)

    @field_validator("args_digest")
    @classmethod
    def _check_digest(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("args_digest must be 64 lowercase hex characters.")
        return value

    @model_validator(mode="after")
    def _check(self) -> ActionRecord:
        if self.outcome == "ok" and self.error_code is not None:
            raise ValueError("a successful action has no error_code.")
        if self.outcome != "ok" and self.error_code is None:
            raise ValueError("a failed or denied action needs an error_code.")
        return self


class Ledger(LedgerModel):
    artifacts: dict[str, Artifact] = Field(default_factory=dict)
    actions: list[ActionRecord] = Field(default_factory=list)

    def next_id(self, origin: IdOrigin) -> str:
        prefix = ID_PREFIXES[origin]
        numbers = [int(key[1:]) for key in self.artifacts if key[0] == prefix]
        return f"{prefix}{max(numbers, default=0) + 1}"

    def add(self, artifact: Artifact) -> Artifact:
        if artifact.id in self.artifacts:
            raise ValueError(f"artifact id {artifact.id} already exists.")
        unknown = [source for source in artifact.sources if source not in self.artifacts]
        if unknown:
            raise ValueError(f"{artifact.id}: unknown source artifacts {', '.join(unknown)}.")
        if self.by_path(artifact.path) is not None:
            raise ValueError(f"{artifact.id}: path is already registered: {_resolved(artifact.path)}")
        if any(self.artifacts[source].untrusted for source in artifact.sources):
            artifact.untrusted = True
        self.artifacts[artifact.id] = artifact
        return artifact

    def get(self, artifact_id: str) -> Artifact | None:
        return self.artifacts.get(artifact_id)

    def by_path(self, path: str | Path) -> Artifact | None:
        target = _resolved(path)
        return next((item for item in self.artifacts.values() if _resolved(item.path) == target), None)

    def set_status(self, artifact_id: str, status: StepStatus, warnings: list[str] | None = None) -> Artifact:
        artifact = self.artifacts[artifact_id]
        artifact.step_status = status
        if warnings:
            artifact.warnings = [*artifact.warnings, *warnings]
        return artifact

    def lineage(self, artifact_id: str) -> list[Artifact]:
        if artifact_id not in self.artifacts:
            raise KeyError(f"unknown artifact: {artifact_id}")
        seen: dict[str, Artifact] = {}
        pending = [artifact_id]
        while pending:
            current = self.artifacts[pending.pop()]
            if current.id in seen:
                continue
            seen[current.id] = current
            pending.extend(current.sources)
        return list(seen.values())

    def record(self, action: ActionRecord) -> ActionRecord:
        unknown = [artifact_id for artifact_id in action.artifacts if artifact_id not in self.artifacts]
        if unknown:
            raise ValueError(f"action {action.seq} references unknown artifacts {', '.join(unknown)}.")
        self.actions.append(action)
        return action

    def failed_before(self, tool: str, args_digest: str) -> ActionRecord | None:
        for action in reversed(self.actions):
            if action.tool != tool or action.args_digest != args_digest or action.outcome == "denied":
                continue
            if action.outcome == "error" and action.retryable is False:
                return action
            return None
        return None

    def uncertain_attempts(self, tool: str, args_digest: str) -> int:
        matching = (action for action in self.actions if action.tool == tool and action.args_digest == args_digest)
        return sum(1 for action in matching if action.uncertain)
