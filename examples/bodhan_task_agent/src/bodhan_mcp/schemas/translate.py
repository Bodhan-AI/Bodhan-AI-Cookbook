from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import Field, model_validator

from bodhan_mcp.core.languages import TranslateTarget
from bodhan_mcp.schemas.base import UNTRUSTED_CONTENT_DESCRIPTION, ToolInput, ToolOutput

TranslateScript = Literal["native", "roman", "codemix"]

_ROMAN_UNSUPPORTED = frozenset({"en"})
_CODEMIX_UNSUPPORTED = frozenset({"en", "ks-Deva", "sd-Arab"})


@dataclass(frozen=True)
class TranslateResult:
    text: str
    target_language: str
    script: TranslateScript
    truncated: bool
    completion_tokens: int | None
    request_id: str | None = None


class TranslateInput(ToolInput):
    text: str | None = Field(
        default=None,
        description="Text to translate. Give either text or text_path, not both. Use text_path for long documents.",
    )
    text_path: str | None = Field(
        default=None,
        description=(
            "Path to a UTF-8 text or Markdown file to translate. Relative paths are resolved against the "
            "server's input folder."
        ),
    )
    target_language: TranslateTarget = Field(
        description="Language to translate into. The source language does not need to be given.",
    )
    script: TranslateScript = Field(
        default="native",
        description=(
            "'native' writes the target language in its own script. 'roman' writes it in Latin letters "
            "(not available for 'en'). 'codemix' mixes in English words (not available for 'en', 'ks-Deva', 'sd-Arab')."
        ),
    )
    output_path: str | None = Field(
        default=None,
        description=(
            "Where to write the full translation, inside the server's output folder. "
            "Defaults to a name derived from the input."
        ),
    )
    overwrite: bool = Field(default=False, description="Replace output_path if it already exists.")

    @model_validator(mode="after")
    def _check(self) -> TranslateInput:
        if (self.text is None) == (self.text_path is None):
            raise ValueError("Provide exactly one of text or text_path.")
        if self.text is not None and not self.text.strip():
            raise ValueError("text must contain non-whitespace characters.")
        if self.script == "roman" and self.target_language in _ROMAN_UNSUPPORTED:
            raise ValueError(f"script 'roman' is not available for '{self.target_language}'.")
        if self.script == "codemix" and self.target_language in _CODEMIX_UNSUPPORTED:
            raise ValueError(f"script 'codemix' is not available for '{self.target_language}'.")
        return self


class TranslateOutput(ToolOutput):
    output_path: str = Field(description="File holding the complete translation.")
    target_language: str
    script: TranslateScript
    translation: str = Field(description="The translation, or its first part when translation_is_preview is true.")
    translation_is_preview: bool = Field(description="True when translation is truncated; read output_path for all.")
    source_chars: int = Field(ge=0)
    translated_chars: int = Field(ge=0)
    chunks: int = Field(ge=0, description="Pieces the source was split into to fit the model's context window.")
    recovery_splits: int = Field(
        ge=0, description="Times a piece was split and retranslated because the model cut its answer short."
    )
    verification_status: Literal["passed", "warning"] = Field(
        description=(
            "Structural checks only: every piece translated and non-empty, Markdown table shape kept, "
            "plausible length. Not a check of translation accuracy."
        ),
    )
    untrusted_content: bool = Field(description=UNTRUSTED_CONTENT_DESCRIPTION)
    warnings: list[str] = Field(default_factory=list)
