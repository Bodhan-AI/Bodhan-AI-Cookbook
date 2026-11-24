"""JSONL dataset and calibration-label loading. Bad lines are reported, never fatal.

Both files are read with the same line reader. The calibration file (``--calibration``)
accepts three line shapes, handled in one pass:

1. a full item (``id``, ``source``, ``lang`` [, outputs], ``labels``): labels attach to the
   matching dataset item, or the line becomes a calibration-only item;
2. ``id`` + ``lang`` + ``labels``: labels attach to exactly that dataset item;
3. ``id`` + ``labels``: labels attach to the one dataset item with that id (ambiguous ids are noted).

Language codes are normalised to the registry's spelling (``hi-IN`` -> ``hi``), with one
warning per distinct code.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import canonical_category, canonical_severity, normalize_lang_code
from .models import Item, Label

log = logging.getLogger(__name__)


@dataclass
class LoadReport:
    bad: list[dict] = field(default_factory=list)  # {"line", "id", "reason"}
    filtered_out: int = 0  # items whose lang is not in --langs
    notes: list[dict] = field(default_factory=list)  # non-fatal problems on kept items
    unknown_label_types: dict[str, int] = field(default_factory=dict)
    lang_aliases: dict[str, str] = field(default_factory=dict)  # raw code as written -> registry code


def _span(v: Any) -> tuple[int, int] | None:
    if isinstance(v, (list, tuple)) and len(v) == 2 and all(isinstance(x, int) for x in v) and 0 <= v[0] <= v[1]:
        return (int(v[0]), int(v[1]))
    return None


def _text(v: Any) -> str | None:
    """A non-blank string, or None."""
    return v if isinstance(v, str) and v.strip() else None


def parse_labels(raw: Any, default_span_on: str, rep: LoadReport, where: str) -> list[Label] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        rep.notes.append({"item": where, "reason": "labels is not a list; ignored"})
        return None
    out: list[Label] = []
    for lab in raw:
        if not isinstance(lab, dict):
            continue
        raw_type = str(lab.get("type", ""))
        cat = canonical_category(raw_type)
        if cat is None:
            rep.unknown_label_types[raw_type] = rep.unknown_label_types.get(raw_type, 0) + 1
        span_on = str(lab.get("span_on") or default_span_on).lower()
        span_on = "source" if span_on == "source" else "translation"
        span = _span(lab.get("span"))
        if span is None and _span(lab.get("source_span")) is not None:
            span, span_on = _span(lab.get("source_span")), "source"
        out.append(
            Label(
                category=cat,
                raw_type=raw_type,
                severity=canonical_severity(lab.get("severity")),
                span=span,
                span_on=span_on,
                annotator=lab.get("annotator"),
            )
        )
    return out


def _read_json_lines(path: Path) -> Iterator[tuple[int, dict | None, str | None]]:
    """Yield ``(line_no, object, error)`` for every non-blank line; exactly one of object/error is set."""
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                yield n, None, f"invalid JSON: {e.msg}"
                continue
            if not isinstance(obj, dict):
                yield n, None, "line is not a JSON object"
                continue
            yield n, obj, None


def _canonical_lang(raw: str, rep: LoadReport) -> str:
    """Registry spelling of a language code; warns once per distinct code that had to change."""
    code = normalize_lang_code(raw)
    if code != raw and raw not in rep.lang_aliases:
        rep.lang_aliases[raw] = code
        log.warning("language code %r normalised to %r", raw, code)
    return code


def _item_from_obj(obj: dict, lang: str, n: int, base: Path, rep: LoadReport, label_span_on: str) -> Item:
    """Build an Item from a validated line (id, source and lang already checked)."""
    key = f"{obj['id']}::{lang}"
    audio_path = None
    audio = _text(obj.get("audio"))
    if audio is not None:
        p = Path(audio)
        p = p if p.is_absolute() else (base / p)
        if p.is_file():
            audio_path = str(p.resolve())
        else:
            rep.notes.append({"item": key, "reason": f"audio file not found: {audio}"})
    return Item(
        id=str(obj["id"]),
        lang=lang,
        source=obj["source"],
        reference=_text(obj.get("reference")),
        translation=_text(obj.get("translation")),
        audio=audio_path,
        labels=parse_labels(obj.get("labels"), label_span_on, rep, key),
        line_no=n,
    )


def _has_required(obj: dict) -> bool:
    return obj.get("id") is not None and _text(obj.get("source")) is not None and _text(obj.get("lang")) is not None


def load_dataset(
    path: str | Path, langs: set[str] | None, label_span_on: str = "translation"
) -> tuple[list[Item], LoadReport]:
    path = Path(path)
    rep = LoadReport()
    items: list[Item] = []
    seen: set[str] = set()
    for n, obj, err in _read_json_lines(path):
        if obj is None:
            rep.bad.append({"line": n, "id": None, "reason": err})
            continue
        if not _has_required(obj):
            rep.bad.append(
                {"line": n, "id": obj.get("id"), "reason": "missing or empty required field (id, source, lang)"}
            )
            continue
        lang = _canonical_lang(obj["lang"].strip(), rep)
        if langs and lang not in langs:
            rep.filtered_out += 1
            continue
        key = f"{obj['id']}::{lang}"
        if key in seen:
            rep.bad.append({"line": n, "id": obj["id"], "reason": f"duplicate id for lang {lang}"})
            continue
        seen.add(key)
        items.append(_item_from_obj(obj, lang, n, path.parent, rep, label_span_on))
    return items, rep


def apply_calibration_file(
    path: str | Path, items: list[Item], langs: set[str] | None, rep: LoadReport, label_span_on: str
) -> list[Item]:
    """Attach labels from ``--calibration`` in one pass over the file (shapes in the module docstring).

    Returns calibration-only items: full lines that are not in the dataset. They are scored and used for
    calibration, but kept out of the main summary. Problems are recorded in ``rep.notes``, never raised.
    """
    path = Path(path)
    by_key = {it.key: it for it in items}
    by_id: dict[str, list[Item]] = {}
    for it in items:
        by_id.setdefault(it.id, []).append(it)
    added: dict[str, Item] = {}
    for n, obj, err in _read_json_lines(path):
        where = f"calibration line {n}"
        if obj is None:
            rep.notes.append({"item": where, "reason": err})
        elif _text(obj.get("source")) is not None:
            _apply_full_line(obj, n, path.parent, by_key, added, langs, rep, label_span_on)
        elif "labels" in obj:
            _apply_label_line(obj, where, by_key, by_id, langs, rep, label_span_on)
        else:
            rep.notes.append({"item": where, "reason": "neither a full item nor a labels line; ignored"})
    return list(added.values())


def _apply_full_line(
    obj: dict,
    n: int,
    base: Path,
    by_key: dict[str, Item],
    added: dict[str, Item],
    langs: set[str] | None,
    rep: LoadReport,
    label_span_on: str,
) -> None:
    """Shape 1: a full item. Its labels replace the dataset item's, or it becomes a calibration-only item."""
    if not _has_required(obj):
        rep.notes.append({"item": f"calibration line {n}", "reason": "missing or empty required field (id, lang)"})
        return
    lang = _canonical_lang(obj["lang"].strip(), rep)
    if langs and lang not in langs:
        return
    it = _item_from_obj(obj, lang, n, base, rep, label_span_on)
    if it.key in by_key:
        if it.labels is not None:
            by_key[it.key].labels = it.labels
    elif it.key in added:
        rep.notes.append({"item": it.key, "reason": f"calibration line {n}: duplicate item; ignored"})
    else:
        it.calibration_only = True
        added[it.key] = it


def _apply_label_line(
    obj: dict,
    where: str,
    by_key: dict[str, Item],
    by_id: dict[str, list[Item]],
    langs: set[str] | None,
    rep: LoadReport,
    label_span_on: str,
) -> None:
    """Shapes 2 and 3: ``id`` (+ ``lang``) + ``labels``, matched to exactly one dataset item."""
    iid = str(obj.get("id"))
    raw_lang = _text(obj.get("lang"))
    if raw_lang is not None:
        lang = _canonical_lang(raw_lang.strip(), rep)
        if langs and lang not in langs:
            return  # labels for a language this run does not score
        target = by_key.get(f"{iid}::{lang}")
        targets = [target] if target is not None else []
    else:
        targets = by_id.get(iid, [])
    if len(targets) != 1:
        rep.notes.append({"item": iid, "reason": f"{where}: cannot match to exactly one dataset item"})
        return
    targets[0].labels = parse_labels(obj.get("labels"), label_span_on, rep, targets[0].key)
