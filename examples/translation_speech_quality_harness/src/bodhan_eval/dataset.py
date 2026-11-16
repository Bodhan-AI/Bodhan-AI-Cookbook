"""Read and validate the JSONL dataset. Bad rows are skipped and counted, never fatal."""

import json
from pathlib import Path

from .config import normalize_lang


class DatasetError(Exception):
    """The dataset file itself cannot be read. The only fatal input error."""


def _text(row, field):
    v = row.get(field)
    return v if isinstance(v, str) and v.strip() else None


def _labels(raw, notes):
    if raw is None:
        return None  # unlabelled; an empty list means "labelled clean"
    if not isinstance(raw, list):
        notes.append("labels ignored: not a list")
        return None
    out = []
    for i, lab in enumerate(raw):
        if not isinstance(lab, dict) or not isinstance(lab.get("type"), str):
            notes.append(f"label {i} ignored: no type")
            continue
        span = lab.get("span")
        if not (isinstance(span, list) and len(span) == 2 and all(isinstance(x, int) for x in span) and 0 <= span[0] <= span[1]):
            if span is not None:
                notes.append(f"label {i}: span ignored")
            span = None
        out.append({**lab, "span": span})
    return out


def load(path: str | Path, langs: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    """Return (items, skipped). Each skipped entry has line, id and reason."""
    path = Path(path)
    try:
        lines = path.read_bytes().decode("utf-8-sig", errors="replace").splitlines()
    except OSError as e:
        raise DatasetError(f"cannot read dataset {path}: {e}") from None

    items, skipped, seen = [], [], set()

    def skip(n, row, reason):
        rid = row.get("id") if isinstance(row, dict) else None
        skipped.append({"line": n, "id": rid if isinstance(rid, (str, int)) else None, "reason": reason})

    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            skip(n, None, "invalid JSON")
            continue
        if not isinstance(row, dict):
            skip(n, None, "not a JSON object")
            continue
        rid = row.get("id")
        if isinstance(rid, bool) or not isinstance(rid, (str, int)) or str(rid).strip() == "":
            skip(n, row, "missing or invalid id")
            continue
        source = _text(row, "source")
        if not source:
            skip(n, row, "missing or empty source")
            continue
        if not isinstance(row.get("lang"), str):
            skip(n, row, "missing lang")
            continue
        lang = normalize_lang(row["lang"])
        if not lang or lang == "en":
            skip(n, row, f"unsupported lang {row['lang']!r}")
            continue
        if langs is not None and lang not in langs:
            skip(n, row, "lang not in --langs")
            continue
        key = (str(rid), lang)
        if key in seen:
            skip(n, row, "duplicate id and lang")
            continue
        seen.add(key)

        notes = []
        if "�" in line:
            notes.append("line contained invalid UTF-8; replaced")
        audio = None
        if isinstance(row.get("audio"), str) and row["audio"].strip():
            p = Path(row["audio"])
            candidates = [p] if p.is_absolute() else [path.parent / p, Path.cwd() / p]
            audio = next((c for c in candidates if c.is_file()), None)
            if audio is None:
                notes.append(f"audio file not found: {row['audio']}")
        items.append({
            "id": str(rid), "lang": lang, "line": n, "source": source,
            "translation": _text(row, "translation"), "reference": _text(row, "reference"),
            "audio": str(audio) if audio else None, "audio_declared": "audio" in row and row["audio"] is not None,
            "labels": _labels(row.get("labels"), notes), "notes": notes,
        })
    return items, skipped
