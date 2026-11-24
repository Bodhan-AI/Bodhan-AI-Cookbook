"""JSONL dataset loader. Bad rows are logged, skipped and counted, never fatal."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .config import LANGUAGES, canonical_category, canonical_severity, is_speech

# -------------------- dataset
log = logging.getLogger("bodhan_eval.dataset")


@dataclass
class Label:
    type: str  # raw type string as given
    category: str  # canonical category, or "unmapped:<raw>"
    severity: str | None = None  # canonical severity (minor/major/critical) or None
    span: tuple[float, float] | None = None
    frame: str | None = None  # "chars" | "seconds" | "chars_or_seconds" (speech label with int span)
    target_field: str | None = None  # "source" | "translation" | "audio" if the label says which text it refers to
    extra: dict = field(default_factory=dict)


@dataclass
class Item:
    id: str
    source: str
    lang: str
    reference: str | None = None
    translation: str | None = None
    audio: Path | None = None
    labels: list[Label] | None = None  # None = unlabelled; [] = labelled clean
    raw: dict = field(default_factory=dict)
    line_no: int = 0
    audio_missing: bool = False  # the row names an audio file that does not exist (speech checks are skipped)


@dataclass
class LoadResult:
    items: list[Item]
    bad: list[dict]
    filtered_out: int
    total_lines: int
    missing_audio: list[str] = field(default_factory=list)  # ids whose named audio file does not exist
    bad_labels: list[dict] = field(default_factory=list)  # labels dropped on their own (row kept)


_SECONDS_UNITS = {"s", "sec", "secs", "second", "seconds", "audio", "time"}


_CHAR_UNITS = {"char", "chars", "character", "characters", "text", "codepoints"}


def _parse_label(obj) -> Label:
    if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
        raise ValueError("label must be an object with a string 'type'")
    category = canonical_category(obj["type"])
    span, frame = obj.get("span"), None
    if span is not None:
        ok = isinstance(span, list | tuple) and len(span) == 2 and all(isinstance(x, int | float) and not isinstance(x, bool) for x in span)
        if not ok or not (0 <= span[0] <= span[1]):
            raise ValueError(f"label span must be [start, end] numbers with 0 <= start <= end, got {span!r}")
        unit = str(obj.get("span_unit") or obj.get("unit") or "").strip().lower()
        if unit in _SECONDS_UNITS or any(isinstance(x, float) for x in span):
            frame = "seconds"
        elif unit in _CHAR_UNITS or not is_speech(category):
            frame = "chars"
        else:
            frame = "chars_or_seconds"
        span = (span[0], span[1]) if frame == "seconds" else (int(span[0]), int(span[1]))
    fld = obj.get("field") or obj.get("target") or obj.get("on")
    fld = str(fld).strip().lower() if isinstance(fld, str) else None
    known = {"type", "severity", "span", "span_unit", "unit", "field", "target", "on"}
    extra = {k: v for k, v in obj.items() if k not in known}
    return Label(type=obj["type"], category=category, severity=canonical_severity(obj.get("severity")), span=span, frame=frame, target_field=fld, extra=extra)


def parse_labels(raw: list) -> tuple[list[Label] | None, list[str]]:
    """Parse a row's ``labels`` list. A bad label is dropped on its own (the row and its other
    labels are kept). If every label was bad, returns None (unlabelled), never [] ("labelled clean")."""
    labels, errors = [], []
    for k, obj in enumerate(raw):
        try:
            labels.append(_parse_label(obj))
        except (ValueError, TypeError) as exc:
            errors.append(f"label {k}: {exc}")
    if raw and not labels:
        return None, errors
    return labels, errors


def _opt_str(row: dict, key: str) -> str | None:
    v = row.get(key)
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError(f"'{key}' must be a string")
    return v if v.strip() else None


def load_dataset(path: str | Path, langs: list[str] | None = None) -> LoadResult:
    path = Path(path)
    base = path.parent
    items: list[Item] = []
    bad: list[dict] = []
    seen: set[str] = set()
    missing_audio: list[str] = []
    bad_labels: list[dict] = []
    filtered = 0
    total = 0
    wanted = set(langs) if langs else None
    with path.open("r", encoding="utf-8-sig") as fh:
        for line_no, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            total += 1
            item_id = None
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("line is not a JSON object")
                item_id = row.get("id")
                if not isinstance(item_id, str | int) or str(item_id).strip() == "":
                    raise ValueError("missing 'id'")
                item_id = str(item_id)
                source = row.get("source")
                if not isinstance(source, str) or not source.strip():
                    raise ValueError("missing or empty 'source'")
                lang = row.get("lang")
                if not isinstance(lang, str) or not lang.strip():
                    raise ValueError("missing 'lang'")
                lang = lang.strip().lower()
                if lang not in LANGUAGES:
                    raise ValueError(f"unsupported lang {lang!r}")
                if wanted is not None and lang not in wanted:
                    filtered += 1
                    continue
                if item_id in seen:
                    raise ValueError(f"duplicate id {item_id!r}")
                labels = None
                if "labels" in row and row["labels"] is not None:
                    if not isinstance(row["labels"], list):
                        raise ValueError("'labels' must be a list")
                    labels, label_errors = parse_labels(row["labels"])
                    for err in label_errors:
                        bad_labels.append({"line": line_no, "id": item_id, "reason": err})
                        log.warning("bad_label", extra={"fields": {"line": line_no, "id": item_id, "reason": err}})
                audio = _opt_str(row, "audio")
                audio_path = None
                if audio:
                    p = Path(audio)
                    audio_path = p if p.is_absolute() else (base / p)
                item = Item(
                    id=item_id,
                    source=source,
                    lang=lang,
                    reference=_opt_str(row, "reference"),
                    translation=_opt_str(row, "translation"),
                    audio=audio_path,
                    labels=labels,
                    raw=row,
                    line_no=line_no,
                )
            except (ValueError, TypeError) as exc:
                bad.append({"line": line_no, "id": item_id, "reason": str(exc)})
                log.warning("bad_item", extra={"fields": {"line": line_no, "id": item_id, "reason": str(exc)}})
                continue
            if item.audio is not None and not item.audio.is_file():
                # a missing file is an input problem, not a bad row: keep the item, skip its speech checks
                log.warning("audio_missing", extra={"fields": {"line": line_no, "id": item_id, "file": item.audio.name}})
                item.audio, item.audio_missing = None, True
                missing_audio.append(item_id)
            seen.add(item_id)
            items.append(item)
    return LoadResult(items=items, bad=bad, filtered_out=filtered, total_lines=total, missing_audio=sorted(missing_audio), bad_labels=bad_labels)
