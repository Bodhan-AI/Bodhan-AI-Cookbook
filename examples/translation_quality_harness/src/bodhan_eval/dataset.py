"""JSONL loading. Malformed rows are recorded and skipped, never fatal."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .models import Item


@dataclass
class BadRow:
    line: int
    id: str | None
    error: str


@dataclass
class LoadedItem:
    uid: str
    item: Item
    audio_path: Path | None
    origin: str  # "dataset" | "calibration"


def _first_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    loc = ".".join(str(p) for p in err.get("loc", ())) or "row"
    return f"{loc}: {err.get('msg')}"


def resolve_audio(audio: str | None, dataset_path: Path) -> Path | None:
    if not audio:
        return None
    candidate = Path(audio)
    if candidate.is_absolute():
        return candidate
    for base in (dataset_path.parent, Path.cwd()):
        if (base / candidate).exists():
            return base / candidate
    return dataset_path.parent / candidate


def load(path: str | Path, origin: str = "dataset") -> tuple[list[LoadedItem], list[BadRow]]:
    path = Path(path)
    items: list[Item] = []
    bad: list[BadRow] = []
    with path.open(encoding="utf-8-sig") as fh:
        for line_no, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                bad.append(BadRow(line_no, None, f"invalid JSON: {exc.msg}"))
                continue
            if not isinstance(row, dict):
                bad.append(BadRow(line_no, None, "row is not a JSON object"))
                continue
            try:
                items.append(Item.model_validate(row))
            except ValidationError as exc:
                bad.append(BadRow(line_no, str(row.get("id")) if row.get("id") is not None else None, _first_error(exc)))
    id_counts = Counter(i.id for i in items)
    pair_counts = Counter((i.id, i.lang) for i in items)
    seen: set[str] = set()
    loaded: list[LoadedItem] = []
    for item in items:
        uid = item.id if id_counts[item.id] == 1 else f"{item.id}.{item.lang}"
        if pair_counts[(item.id, item.lang)] > 1 and uid in seen:
            bad.append(BadRow(0, item.id, f"duplicate id {item.id!r} for lang {item.lang!r}; later row skipped"))
            continue
        seen.add(uid)
        prefix = "cal:" if origin == "calibration" else ""
        loaded.append(LoadedItem(prefix + uid, item, resolve_audio(item.audio, path), origin))
    return loaded, bad
