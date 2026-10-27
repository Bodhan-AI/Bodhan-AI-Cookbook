"""JSON and CSV export, all built from the same ``CallRecord``.

CSV files are UTF-8 with a BOM so Excel shows Indian scripts correctly.
"""

from __future__ import annotations

import csv
import io
import json

from .models import CallRecord

CALLS_COLUMNS = [
    "call_id", "source_file", "ticket_id", "agent_name", "duration_seconds", "source_language",
    "processed_at", "status", "summary", "customer_issue", "resolution", "action_items", "topics",
    "overall_sentiment", "sentiment_score", "customer_sentiment_trend", "transcript_english", "transcript_original",
]  # fmt: skip
SEGMENTS_COLUMNS = ["call_id", "segment_no", "start", "end", "speaker", "sentiment", "original_text", "english_text"]

EXPORT_FORMATS = {
    "json": ("application/json", "{call_id}.json"),
    "calls_csv": ("text/csv; charset=utf-8", "{call_id}_calls.csv"),
    "segments_csv": ("text/csv; charset=utf-8", "{call_id}_segments.csv"),
}


def to_json(record: CallRecord) -> str:
    return json.dumps(record.model_dump(mode="json"), ensure_ascii=False, indent=2)


def to_calls_csv(record: CallRecord) -> str:
    row = {
        "call_id": record.call_id,
        "source_file": record.source_file,
        "ticket_id": record.ticket_id,
        "agent_name": record.agent_name,
        "duration_seconds": record.duration_seconds,
        "source_language": record.source_language,
        "processed_at": record.processed_at.isoformat(),
        "status": record.status.value,
        **_analysis_cells(record),
        "transcript_english": record.full_text_english,
        "transcript_original": record.full_text_original,
    }
    return _csv(CALLS_COLUMNS, [row])


def _analysis_cells(record: CallRecord) -> dict:
    a = record.analysis
    if a is None:
        return {}
    return {
        "summary": a.summary,
        "customer_issue": a.customer_issue,
        "resolution": a.resolution.value,
        "action_items": "\n".join(a.action_items),
        "topics": "; ".join(a.topics),
        "overall_sentiment": a.overall_sentiment.value,
        "sentiment_score": a.sentiment_score,
        "customer_sentiment_trend": a.customer_sentiment_trend.value,
    }


def to_segments_csv(record: CallRecord) -> str:
    rows = [{"call_id": record.call_id, **s.model_dump(include=set(SEGMENTS_COLUMNS))} for s in record.segments]
    return _csv(SEGMENTS_COLUMNS, rows)


def export(record: CallRecord, fmt: str) -> tuple[bytes, str, str]:
    """Return (body, media type, file name) for one of ``EXPORT_FORMATS``."""
    media_type, name = EXPORT_FORMATS[fmt]
    text = {"json": to_json, "calls_csv": to_calls_csv, "segments_csv": to_segments_csv}[fmt](record)
    encoding = "utf-8" if fmt == "json" else "utf-8-sig"
    return text.encode(encoding), media_type, name.format(call_id=record.call_id)


def _csv(columns: list[str], rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\r\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: _cell(row.get(k)) for k in columns})
    return buf.getvalue()


def _cell(value: object) -> object:
    """Empty for None, and neutralise text that a spreadsheet would run as a formula."""
    if value is None:
        return ""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value
