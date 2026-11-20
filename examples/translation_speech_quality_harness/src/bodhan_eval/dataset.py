"""JSONL dataset loading. Bad or partial items are logged and skipped, never fatal."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bodhan_eval.categories import canonical_category, canonical_severity
from bodhan_eval.languages import Language

_AREA_KEYS = ("area", "target", "modality", "field", "on", "kind")


@dataclass
class Label:
    category: str
    severity: str
    span: tuple[int, int] | None
    raw: dict[str, Any]
    annotator: str | None = None


@dataclass
class Item:
    id: str
    source: str
    lang: str
    reference: str | None = None
    translation: str | None = None
    audio: Path | None = None
    labels: list[Label] | None = None  # None = unlabelled; [] = labelled clean
    extra: dict[str, Any] = field(default_factory=dict)
    line_no: int = 0

    @property
    def is_labelled(self) -> bool:
        return self.labels is not None


@dataclass
class Problem:
    line_no: int
    item_id: str | None
    reason: str
    fatal: bool  # True = item skipped; False = item kept, something dropped


@dataclass
class LoadResult:
    items: list[Item]
    problems: list[Problem]
    filtered_out: int  # valid items whose lang was not requested


def _opt_str(v: Any) -> str | None:
    return v if isinstance(v, str) and v.strip() else None


def _parse_span(v: Any) -> tuple[int, int] | None:
    if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, int) for x in v):
        s, e = v
        if 0 <= s <= e:
            return (s, e)
    if isinstance(v, dict) and isinstance(v.get("start"), int) and isinstance(v.get("end"), int):
        return _parse_span([v["start"], v["end"]])
    return None


def parse_labels(
    raw: Any, line_no: int, item_id: str, problems: list[Problem]
) -> list[Label] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        problems.append(Problem(line_no, item_id, "labels is not a list; ignored", False))
        return None
    out: list[Label] = []
    for lab in raw:
        if not isinstance(lab, dict):
            problems.append(Problem(line_no, item_id, f"label not an object: {lab!r}", False))
            continue
        raw_type = lab.get("type") or lab.get("category") or ""
        area = next((str(lab[k]) for k in _AREA_KEYS if isinstance(lab.get(k), str)), None)
        cat = canonical_category(str(raw_type), area=area)
        if cat is None:
            problems.append(Problem(line_no, item_id, f"unknown label type {raw_type!r}", False))
            continue
        out.append(
            Label(
                category=cat,
                severity=canonical_severity(lab.get("severity")),
                span=_parse_span(lab.get("span")),
                raw=lab,
                annotator=lab.get("annotator"),
            )
        )
    return out


def load_dataset(
    path: str | Path,
    registry: dict[str, Language],
    langs: list[str] | None = None,
) -> LoadResult:
    path = Path(path)
    base = path.parent
    items: list[Item] = []
    problems: list[Problem] = []
    seen: set[str] = set()
    filtered = 0
    lookup = {k.lower(): k for k in registry}
    wanted = {lang.lower() for lang in langs} if langs else None

    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                problems.append(Problem(line_no, None, f"invalid JSON: {e.msg}", True))
                continue
            if not isinstance(obj, dict):
                problems.append(Problem(line_no, None, "line is not a JSON object", True))
                continue

            item_id = obj.get("id")
            item_id = str(item_id) if isinstance(item_id, (str, int)) and str(item_id) else None
            source = _opt_str(obj.get("source"))
            lang = _opt_str(obj.get("lang"))
            missing = [k for k, v in (("id", item_id), ("source", source), ("lang", lang)) if not v]
            if missing:
                problems.append(
                    Problem(line_no, item_id, f"missing required field(s): {missing}", True)
                )
                continue
            if item_id in seen:
                problems.append(Problem(line_no, item_id, "duplicate id", True))
                continue
            canon_lang = lookup.get(lang.strip().lower())
            if canon_lang is None:
                problems.append(Problem(line_no, item_id, f"unsupported language {lang!r}", True))
                continue
            if wanted is not None and canon_lang.lower() not in wanted:
                filtered += 1
                continue
            seen.add(item_id)

            audio = None
            if _opt_str(obj.get("audio")):
                audio = Path(obj["audio"])
                if not audio.is_absolute():
                    audio = base / audio
                if not audio.exists():
                    problems.append(
                        Problem(line_no, item_id, f"audio file not found: {obj['audio']}", False)
                    )
                    audio = None

            known = {"id", "source", "lang", "reference", "translation", "audio", "labels"}
            items.append(
                Item(
                    id=item_id,
                    source=source,
                    lang=canon_lang,
                    reference=_opt_str(obj.get("reference")),
                    translation=_opt_str(obj.get("translation")),
                    audio=audio,
                    labels=parse_labels(obj.get("labels"), line_no, item_id, problems),
                    extra={k: v for k, v in obj.items() if k not in known},
                    line_no=line_no,
                )
            )
    return LoadResult(items, problems, filtered)
