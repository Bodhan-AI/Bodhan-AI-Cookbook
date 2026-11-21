from __future__ import annotations

import csv
import json
import random
from pathlib import Path
from typing import Iterable


TEXT_LABEL_TYPES = {
    "omission",
    "addition",
    "mistranslation",
    "number",
    "term",
    "format",
    "language",
}
SPEECH_LABEL_TYPES = {
    "speech_omission",
    "repetition",
    "garbled",
}
LABEL_TYPES = TEXT_LABEL_TYPES | SPEECH_LABEL_TYPES
SEVERITIES = {"minor", "major", "critical"}


def build_label_queue(
    items: Iterable[dict],
    *,
    seed: int,
    total: int = 80,
) -> list[dict]:
    rng = random.Random(seed)
    grouped: dict[str, list[dict]] = {"hi": [], "ta": [], "bn": []}
    for item in items:
        lang = str(item.get("lang", "")).lower()
        if lang in grouped:
            grouped[lang].append(dict(item))
    targets = {"hi": 40, "ta": 20, "bn": 20}
    if total != 80:
        raise ValueError("label queue currently requires exactly 80 items")
    queue: list[dict] = []
    for lang in ("hi", "ta", "bn"):
        candidates = grouped[lang]
        rng.shuffle(candidates)
        for item in candidates[: targets[lang]]:
            queue.append({
                "id": str(item.get("id", "")),
                "lang": lang,
                "source": str(item.get("source", "")),
                "translation": str(item.get("translation", "")),
                "candidate_category": "",
                "candidate_severity": "",
                "type": "",
                "severity": "",
                "span_start": "",
                "span_end": "",
                "reviewed_clean": "",
                "notes": "",
            })
    return queue


def write_label_queue(path: Path, rows: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "id",
        "lang",
        "source",
        "translation",
        "candidate_category",
        "candidate_severity",
        "type",
        "severity",
        "span_start",
        "span_end",
        "reviewed_clean",
        "notes",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _record_from_row(row: dict[str, str]) -> dict:
    item_id = str(row.get("id", "")).strip()
    lang = str(row.get("lang", "")).strip().lower()
    source = str(row.get("source", ""))
    translation = str(row.get("translation", ""))
    if not item_id or not lang:
        raise ValueError("label row requires id and lang")
    if not source:
        raise ValueError(f"label row {item_id}: source is required")

    reviewed_clean = str(row.get("reviewed_clean", "")).strip().lower()
    label_type = str(row.get("type", "")).strip().lower()
    severity = str(row.get("severity", "")).strip().lower()
    labels: list[dict] = []
    if reviewed_clean == "true":
        if label_type or severity:
            raise ValueError(
                f"label row {item_id}: reviewed_clean=true must not have a label"
            )
    elif reviewed_clean == "false":
        if label_type not in LABEL_TYPES:
            raise ValueError(
                f"label row {item_id}: unsupported label type: {label_type or '<missing>'}"
            )
        if severity not in SEVERITIES:
            raise ValueError(
                f"label row {item_id}: unsupported severity: {severity or '<missing>'}"
            )
        start_text = str(row.get("span_start", "")).strip()
        end_text = str(row.get("span_end", "")).strip()
        if not start_text or not end_text:
            raise ValueError(f"label row {item_id}: span_start and span_end are required")
        start = int(start_text)
        end = int(end_text)
        if start < 0 or end < start:
            raise ValueError(f"label row {item_id}: invalid span [{start}, {end})")
        if label_type in SPEECH_LABEL_TYPES:
            span: list[float] | list[int] = [float(start), float(end)]
        else:
            if end > len(translation):
                raise ValueError(
                    f"label row {item_id}: text span exceeds translation code points"
                )
            span = [start, end]
        labels.append({
            "type": label_type,
            "severity": severity,
            "span": span,
            "annotator": "self",
            "notes": str(row.get("notes", "")).strip(),
        })
    else:
        raise ValueError(
            f"label row {item_id}: reviewed_clean must be true or false"
        )

    record = {
        "id": item_id,
        "lang": lang,
        "source": source,
    }
    if translation:
        record["translation"] = translation
    if labels:
        record["labels"] = labels
    else:
        record["labels"] = []
    return record


def ingest_label_csv(csv_path: Path, output_path: Path) -> dict:
    csv_path = Path(csv_path)
    output_path = Path(output_path)
    records: list[dict] = []
    ids: set[str] = set()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "id",
            "lang",
            "source",
            "translation",
            "type",
            "severity",
            "span_start",
            "span_end",
            "reviewed_clean",
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                "label CSV missing required columns: " + ",".join(sorted(missing))
            )
        for line_number, row in enumerate(reader, start=2):
            try:
                record = _record_from_row(row)
            except ValueError as error:
                raise ValueError(f"CSV line {line_number}: {error}") from error
            if record["id"] in ids:
                raise ValueError(f"duplicate label row id: {record['id']}")
            ids.add(record["id"])
            records.append(record)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        for record in sorted(records, key=lambda record: (record["lang"], record["id"]))
    )
    output_path.write_text(content, encoding="utf-8", newline="\n")
    return {
        "labelled_items": len(records),
        "positive_items": sum(bool(record["labels"]) for record in records),
        "clean_items": sum(not record["labels"] for record in records),
    }
