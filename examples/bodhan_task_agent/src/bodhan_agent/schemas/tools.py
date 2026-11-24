from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bodhan_mcp.core.languages import TranslateTarget
from bodhan_mcp.schemas.base import ToolInput

READ_LIMIT = 12_000
WRITE_LIMIT = 50_000
_DELIVERABLE_ID_RE = re.compile(r"D[1-9]\d*")


def _not_blank(value: str, field: str) -> str:
    if not value.strip():
        raise ValueError(f"{field} cannot be blank.")
    return value


class ReadInput(ToolInput):
    path: str = Field(
        min_length=1,
        description="Absolute path of an English text file listed in the inputs or returned by an earlier tool.",
    )
    offset: int = Field(default=0, ge=0, description="Character position to start reading from, for long files.")
    max_chars: int = Field(default=READ_LIMIT, ge=1, le=READ_LIMIT, description="How many characters to read.")


class WriteInput(ToolInput):
    path: str = Field(
        min_length=1,
        description=(
            "File name such as 'summary.en.md', or a path inside the run's work or artifacts folder. "
            "Existing files are never replaced."
        ),
    )
    content: str = Field(
        min_length=1,
        max_length=WRITE_LIMIT,
        description="English text you wrote. Never Indian-language text; use translate_text for that.",
    )

    @field_validator("content")
    @classmethod
    def _check_content(cls, value: str) -> str:
        return _not_blank(value, "content")


class DetectLanguageInput(ToolInput):
    path: str = Field(min_length=1, description="Absolute path of a text file whose language is unclear.")


class MakeSubtitlesInput(ToolInput):
    segments_path: str = Field(min_length=1, description="The segments_path returned by transcribe_audio.")
    target_language: TranslateTarget = Field(description="Language of the subtitles, e.g. 'en'.")
    output_path: str | None = Field(
        default=None,
        description="File name ending in .srt; defaults to a name derived from the segments file.",
    )

    @model_validator(mode="after")
    def _check(self) -> MakeSubtitlesInput:
        if self.output_path is not None and not self.output_path.strip().lower().endswith(".srt"):
            raise ValueError("output_path must end in .srt.")
        return self


class DeliverableRef(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(description="Deliverable id from the plan, e.g. D1.")
    path: str | None = Field(
        default=None,
        description="Id (such as A4) or path of the file that fulfils it; empty for an answer in result.md.",
    )

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _DELIVERABLE_ID_RE.fullmatch(value):
            raise ValueError("deliverable id must look like D1, D2, ...")
        return value


class FinishInput(ToolInput):
    answer_en: str = Field(
        min_length=1,
        description="Final answer for the user in English: what was done, results, and any limitation.",
    )
    assumptions: list[str] = Field(default_factory=list, description="Assumptions made during the run, in English.")
    deliverables: list[DeliverableRef] = Field(
        default_factory=list,
        description="Each planned deliverable and the file that fulfils it.",
    )

    @field_validator("answer_en")
    @classmethod
    def _check_answer(cls, value: str) -> str:
        return _not_blank(value, "answer_en")

    @model_validator(mode="after")
    def _check(self) -> FinishInput:
        ids = [item.id for item in self.deliverables]
        if len(ids) != len(set(ids)):
            raise ValueError("each deliverable id may appear only once.")
        return self


class LocalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: dict[str, Any] = Field(default_factory=dict, description="Fields shown to the LLM; 'text' is wrapped.")
    artifact_ids: list[str] = Field(default_factory=list, description="Ledger ids created or used by the tool.")
    usage: dict[str, Any] | None = Field(
        default=None,
        description="Bodhan usage summed over the MCP calls the tool made, in the server's usage shape.",
    )
