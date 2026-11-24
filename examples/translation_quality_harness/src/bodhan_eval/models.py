"""Data contracts shared by every stage of the harness."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Span = tuple[int, int]


class Category(StrEnum):
    OMISSION = "omission"
    ADDITION = "addition"
    MISTRANSLATION = "mistranslation"
    NUMBER = "number"
    TERM = "term"
    FORMAT = "format"
    WRONG_LANGUAGE = "wrong_language"
    SPEECH_OMISSION = "speech_omission"
    SPEECH_REPETITION = "speech_repetition"
    SPEECH_GARBLED = "speech_garbled"

    @property
    def area(self) -> str:
        return "speech" if self.value.startswith("speech_") else "translation"


TRANSLATION_CATEGORIES = [c for c in Category if c.area == "translation"]
SPEECH_CATEGORIES = [c for c in Category if c.area == "speech"]


class Severity(StrEnum):
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"

    @property
    def weight(self) -> int:
        return {"minor": 1, "major": 5, "critical": 10}[self.value]


SEVERITY_ALIASES = {"low": "minor", "medium": "major", "high": "critical", "neutral": "minor", "moderate": "major", "severe": "critical"}

# Label type strings seen in the wild are mapped onto our categories. "omission" is ambiguous
# between text and speech; the scorer resolves it with the label's `area` or accepts either.
CATEGORY_ALIASES: dict[str, str] = {
    "omission": "omission",
    "deletion": "omission",
    "missing": "omission",
    "addition": "addition",
    "insertion": "addition",
    "hallucination": "addition",
    "mistranslation": "mistranslation",
    "meaning": "mistranslation",
    "accuracy": "mistranslation",
    "number": "number",
    "numbers": "number",
    "number_or_unit": "number",
    "number_unit": "number",
    "unit": "number",
    "date": "number",
    "quantity": "number",
    "term": "term",
    "terminology": "term",
    "entity": "term",
    "term_or_entity": "term",
    "name": "term",
    "named_entity": "term",
    "format": "format",
    "formatting": "format",
    "markdown": "format",
    "latex": "format",
    "table": "format",
    "wrong_language": "wrong_language",
    "wrong_script": "wrong_language",
    "wrong_language_or_script": "wrong_language",
    "language": "wrong_language",
    "script": "wrong_language",
    "untranslated": "wrong_language",
    "speech_omission": "speech_omission",
    "truncation": "speech_omission",
    "omission_or_truncation": "speech_omission",
    "speech_truncation": "speech_omission",
    "speech_omission_or_truncation": "speech_omission",
    "repetition": "speech_repetition",
    "speech_repetition": "speech_repetition",
    "loop": "speech_repetition",
    "garbled": "speech_garbled",
    "garbled_audio": "speech_garbled",
    "speech_garbled": "speech_garbled",
    "noise": "speech_garbled",
    "silence": "speech_garbled",
    "unintelligible": "speech_garbled",
}


def normalize_category(raw: str, area: str | None = None) -> Category | None:
    key = raw.strip().lower().replace("-", "_").replace(" ", "_").replace("/", "_or_")
    if area and area.lower() == "speech" and not key.startswith("speech_"):
        speech_key = f"speech_{key}"
        if speech_key in CATEGORY_ALIASES:
            key = speech_key
    mapped = CATEGORY_ALIASES.get(key)
    return Category(mapped) if mapped else None


def normalize_severity(raw: str | None) -> Severity:
    if not raw:
        return Severity.MAJOR
    key = str(raw).strip().lower()
    key = SEVERITY_ALIASES.get(key, key)
    try:
        return Severity(key)
    except ValueError:
        return Severity.MAJOR


class Label(BaseModel):
    """A human or injector annotation. Unknown extra keys are preserved."""

    model_config = ConfigDict(extra="allow")

    type: str
    severity: str | None = None
    span: Span | None = None
    field: str | None = None
    area: str | None = None
    annotator: str | None = None

    @field_validator("span", mode="before")
    @classmethod
    def _coerce_span(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, dict):
            value = [value.get("start"), value.get("end")]
        if isinstance(value, list | tuple) and len(value) == 2 and all(isinstance(v, int | float) for v in value):
            start, end = int(value[0]), int(value[1])
            return (min(start, end), max(start, end))
        return None

    @property
    def category(self) -> Category | None:
        return normalize_category(self.type, self.area)

    @property
    def ambiguous_omission(self) -> bool:
        return self.type.strip().lower() == "omission" and not self.area

    @property
    def sev(self) -> Severity:
        return normalize_severity(self.severity)


class Item(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    source: str
    lang: str
    reference: str | None = None
    translation: str | None = None
    audio: str | None = None
    labels: list[Label] = Field(default_factory=list)

    @field_validator("id", mode="before")
    @classmethod
    def _id_to_str(cls, value: Any) -> Any:
        return str(value) if isinstance(value, int | float) else value

    @field_validator("lang", mode="before")
    @classmethod
    def _lang(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("labels", mode="before")
    @classmethod
    def _labels(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, list):
            return [v for v in value if isinstance(v, dict) and v.get("type")]
        return []

    @model_validator(mode="after")
    def _non_empty(self) -> Item:
        if not self.id.strip():
            raise ValueError("id is empty")
        if not self.source.strip():
            raise ValueError("source is empty")
        if not self.lang:
            raise ValueError("lang is empty")
        return self

    @property
    def has_labels_field(self) -> bool:
        return "labels" in self.model_fields_set


class Signal(BaseModel):
    """One piece of evidence from one check, before fusion."""

    check: str
    category: Category
    score: float
    severity: Severity = Severity.MAJOR
    span: Span | None = None
    source_span: Span | None = None
    audio_span: tuple[float, float] | None = None
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    hard: bool = False


class JudgeVerdict(BaseModel):
    category: Category
    severity: Severity
    confidence: float
    span: Span | None = None
    source_span: Span | None = None
    explanation: str
    confirms: list[str] = Field(default_factory=list)


class Flag(BaseModel):
    category: Category
    area: str
    severity: Severity
    confidence: float
    span: Span | None = None
    source_span: Span | None = None
    audio_span: tuple[float, float] | None = None
    excerpt: str | None = None
    source_excerpt: str | None = None
    evidence: list[str]
    checks: list[str]
    judge: str | None = None


class CheckResult(BaseModel):
    name: str
    stage: str
    status: str = "ok"
    reason: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    signals: int = 0
    cache: str | None = None


class ItemResult(BaseModel):
    id: str
    lang: str
    status: str
    error: str | None = None
    content_type: str = "text"
    translation: str | None = None
    back_translation: str | None = None
    transcript: str | None = None
    audio: str | None = None
    audio_duration_s: float | None = None
    checks: list[CheckResult] = Field(default_factory=list)
    flags: list[Flag] = Field(default_factory=list)
    rejected: list[Flag] = Field(default_factory=list)
    mqm_penalty: float = 0.0
    quality_score: float | None = None
    labels: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
