"""Dataset loading. Bad lines never stop a run: they are recorded as problems and skipped."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from .taxonomy import canonical_category

log = logging.getLogger(__name__)


class Label(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    severity: str = "major"
    span: list[int] | None = None
    span_field: str = "translation"  # which text the span indexes: translation | source
    source_span: list[int] | None = None
    annotator: str | None = None

    @property
    def category(self) -> str:
        return canonical_category(self.type, getattr(self, "area", None))

    @field_validator("span", "source_span")
    @classmethod
    def _span_ok(cls, v):
        if v is None:
            return v
        if len(v) != 2 or v[0] < 0 or v[1] < v[0]:
            raise ValueError(f"bad span {v}")
        return [int(v[0]), int(v[1])]


class Item(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    source: str
    lang: str
    reference: str | None = None
    translation: str | None = None
    audio: str | None = None
    labels: list[Label] | None = None  # None = unlabelled; [] = verified clean
    audio_segments: list[dict[str, Any]] | None = None

    @field_validator("id", "source", "lang")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("must be a non-empty string")
        return v

    @property
    def labelled(self) -> bool:
        return self.labels is not None


def resolve_audio(item: Item, dataset_dir: Path) -> Path | None:
    if not item.audio:
        return None
    p = Path(item.audio)
    if not p.is_absolute():
        p = dataset_dir / p
    return p


def load_dataset(path: Path, langs: list[str] | None = None) -> tuple[list[Item], list[dict]]:
    """Return (items, problems). `problems` lists skipped lines with a reason."""
    items: list[Item] = []
    problems: list[dict] = []
    seen: set[str] = set()
    with open(path, encoding="utf-8-sig") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as e:
                problems.append({"line": lineno, "id": None, "reason": f"invalid JSON: {e.msg}"})
                continue
            if not isinstance(raw, dict):
                problems.append({"line": lineno, "id": None, "reason": "line is not a JSON object"})
                continue
            try:
                item = Item.model_validate(raw)
            except ValidationError as e:
                first = e.errors()[0]
                problems.append({"line": lineno, "id": raw.get("id"), "reason": f"invalid item: {'.'.join(map(str, first['loc']))}: {first['msg']}"})
                continue
            if item.id in seen:
                problems.append({"line": lineno, "id": item.id, "reason": "duplicate id (kept the first)"})
                continue
            seen.add(item.id)
            if langs and item.lang not in langs:
                continue  # filtered by --langs; not a problem
            items.append(item)
    for p in problems:
        log.warning("skipped item", extra={"event": "dataset.skip", **p})
    return items, problems


def load_labels(path: Path) -> dict[str, list[Label] | None]:
    """Calibration labels: JSONL of {"id", "labels"} (extra fields ignored)."""
    out: dict[str, list[Label] | None] = {}
    with open(path, encoding="utf-8-sig") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                labels = raw.get("labels")
                out[str(raw["id"])] = None if labels is None else [Label.model_validate(x) for x in labels]
            except (json.JSONDecodeError, KeyError, ValidationError) as e:
                log.warning("bad calibration line", extra={"event": "calibration.skip", "reason": str(e)[:200]})
    return out
