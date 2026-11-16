"""Dataset, evidence, and label contracts shared by the CLI and demo tooling."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VOICES = {
    "as": "Prastuti",
    "bn": "Ishita",
    "en": "Arun",
    "gu": "Dhara",
    "hi": "Kavya",
    "kn": "Deepika",
    "ml": "Lakshmi",
    "mr": "Anagha",
    "or": "Itishree",
    "pa": "Kaur",
    "ta": "Anitha",
    "te": "Sravani",
    "ur": "Saba",
}
TEXT_CATEGORIES = (
    "omission",
    "addition",
    "mistranslation",
    "number",
    "entity",
    "format",
    "wrong_language",
)
SPEECH_CATEGORIES = ("speech_omission", "speech_repetition", "garbled_audio")
CATEGORIES = (*TEXT_CATEGORIES, *SPEECH_CATEGORIES)
FIELDS = {"source", "translation", "reference", "transcript", "audio_ms"}
SEVERITIES = {"minor", "major", "critical"}
LABEL_ALIASES = {
    "number_or_unit": "number",
    "term_or_entity": "entity",
    "wrong_language_or_script": "wrong_language",
    "language": "wrong_language",
    "script": "wrong_language",
    "term": "entity",
    "unit": "number",
    "truncation": "speech_omission",
    "omission_or_truncation": "speech_omission",
    "repetition": "speech_repetition",
    "garbled": "garbled_audio",
}


@dataclass(frozen=True)
class Span:
    """Half-open original Unicode code-point offsets, or milliseconds for audio."""

    field: str | None
    start: int
    end: int


@dataclass(frozen=True)
class Label:
    """One known defect, optionally localized by a human or the injector."""

    category: str
    severity: str
    span: Span | None


@dataclass(frozen=True)
class Item:
    """An English source and the supplied material to evaluate for one language."""

    id: str
    source: str
    lang: str
    translation: str | None
    reference: str | None
    audio: str | None
    labels: tuple[Label, ...] | None
    seed_labels: tuple[Label, ...] | None
    label_origin: str
    split: str | None
    line: int

    @property
    def key(self) -> tuple[str, str]:
        """Return the identity shared by calibration and baseline records."""
        return self.id, self.lang


@dataclass(frozen=True)
class Finding:
    """A proposed defect with checked evidence and an ordinal evidence strength."""

    category: str
    severity: str
    span: Span
    check: str
    evidence: str
    detail: str
    strength: float


@dataclass
class Evaluation:
    """Accumulate check outcomes and candidate findings for an item before reporting."""

    item: Item
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    candidates: list[Finding] = field(default_factory=list)
    translation: str | None = None
    transcript: str | None = None
    audio_digest: str | None = None
    audio_file: str | None = None
    usage: dict[str, dict[str, Any]] = field(default_factory=dict)

    def record(self, name: str, status: str = "ok", **details: Any) -> None:
        """Store one check's terminal status together with any metrics or reason."""
        self.checks[name] = {"status": status, **details}


def read_jsonl(path: Path):
    """Yield line numbers and parsed objects or errors without losing subsequent rows."""
    with path.open("rb") as stream:
        for number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    row = json.loads(line.decode("utf-8-sig" if number == 1 else "utf-8"))
                    if not isinstance(row, dict):
                        raise ValueError("expected a JSON object")
                except ValueError as error:
                    yield number, None, str(error)
                else:
                    yield number, row, None


def parse_labels(raw: Any) -> tuple[Label, ...]:
    """Validate documented label types and preserve unspecified span coordinates."""
    if not isinstance(raw, list):
        raise ValueError("labels must be an array")
    labels = []
    for value in raw:
        if not isinstance(value, dict):
            raise ValueError("each label must be an object")
        category = value.get("type", value.get("category"))
        if not isinstance(category, str):
            raise ValueError("label type must be a string")
        category = category.strip().lower().replace("-", "_").replace(" ", "_")
        category = LABEL_ALIASES.get(category, category)
        severity = value.get("severity")
        if category not in CATEGORIES or severity not in sorted(SEVERITIES):
            raise ValueError("unknown label category or severity")
        offsets = value.get("span")
        axis = value.get("field")
        if isinstance(offsets, dict):
            axis = offsets.get("field", axis)
            offsets = [offsets.get("start"), offsets.get("end")]
        span = None
        if offsets is not None:
            if (
                not isinstance(offsets, list)
                or len(offsets) != 2
                or any(type(offset) is not int for offset in offsets)
                or not 0 <= offsets[0] < offsets[1]
                or (axis is not None and axis not in sorted(FIELDS))
            ):
                raise ValueError("label span must be a valid half-open interval")
            span = Span(axis, *offsets)
        labels.append(Label(category, severity, span))
    return tuple(labels)


def parse_item(row: dict[str, Any], line: int) -> Item:
    """Validate the required fields and leave absent candidates as None."""
    for name in ("id", "source", "lang"):
        if not isinstance(row.get(name), str) or not row[name].strip():
            raise ValueError(f"{name} is required and must be a nonempty string")
    for name in ("translation", "reference", "audio"):
        if row.get(name) is not None and not isinstance(row[name], str):
            raise ValueError(f"{name} must be a string or null")
    for name in ("id", "source", "translation", "reference", "audio"):
        if name in row and row[name] is not None:
            row[name].encode("utf-8")  # JSON may encode unpaired surrogate values.
    labels = parse_labels(row["labels"]) if row.get("labels") is not None else None
    origin = row.get("label_origin", "provided")
    if origin not in ("provided", "human", "injected"):
        raise ValueError("label_origin must be provided, human or injected")
    split = row.get("split")
    if split not in (None, "calibration", "holdout"):
        raise ValueError("split must be calibration or holdout")
    lang = row["lang"].lower()
    if lang not in VOICES:
        raise ValueError(f"unsupported production language {lang!r}")
    return Item(
        row["id"],
        row["source"],
        lang,
        row.get("translation"),
        row.get("reference"),
        row.get("audio"),
        labels,
        labels if origin == "injected" else None,
        origin,
        split,
        line,
    )


def load_items(path: Path, languages: set[str]) -> tuple[list[Item], list[dict[str, Any]]]:
    """Load selected rows once and report invalid entries with file line numbers."""
    items, invalid = [], []
    seen = set()
    for line, row, error in read_jsonl(path):
        if error is None:
            try:
                item = parse_item(row, line)
                if item.lang not in languages:
                    continue
                if item.key in seen:
                    raise ValueError(f"duplicate item {item.key!r}")
                seen.add(item.key)
                items.append(item)
            except ValueError as problem:
                error = str(problem)
        if error is not None:
            invalid.append({"line": line, "error": error})
    return items, invalid
