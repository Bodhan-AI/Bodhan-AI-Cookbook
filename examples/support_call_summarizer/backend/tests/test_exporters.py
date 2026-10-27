import csv
import io
import json
from datetime import datetime, timedelta, timezone

from app.exporters import CALLS_COLUMNS, SEGMENTS_COLUMNS, export
from app.models import CallAnalysis, CallRecord, CallStatus, Resolution, Segment, Sentiment, SentimentTrend

IST = timezone(timedelta(hours=5, minutes=30))


def record() -> CallRecord:
    segments = [
        Segment(
            segment_no=1,
            start=0.0,
            end=6.2,
            speaker="Speaker 1",
            original_text="नमस्ते, मेरा इंटरनेट दो दिन से बंद है।",
            english_text="Hello, my internet has not been working for two days.",
            sentiment=Sentiment.negative,
        ),
        Segment(
            segment_no=2, start=6.2, end=9.0, original_text="=SUM(A1)", english_text=None, error="Translation failed"
        ),
        Segment(segment_no=3, start=9.5, end=12.0, original_text='வணக்கம், "சரி", ok', english_text='Hello, "okay", ok'),
    ]
    return CallRecord(
        call_id="c7f3a2e1",
        source_file="call_2026_10_10_1432.m4a",
        ticket_id="TKT-10452",
        agent_name="Agent-07",
        duration_seconds=312.4,
        source_language="hi",
        processed_at=datetime(2026, 10, 15, 14, 32, 10, tzinfo=IST),
        status=CallStatus.partial,
        segments=segments,
        full_text_original="line one\nline two",
        full_text_english="line one\nline two",
    )


def read_csv(body: bytes) -> list[dict]:
    assert body.startswith(b"\xef\xbb\xbf"), "CSV must start with a UTF-8 BOM for Excel"
    return list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))


def test_json_keeps_indian_scripts_and_spec_fields():
    body, media_type, name = export(record(), "json")
    assert media_type == "application/json"
    assert name == "c7f3a2e1.json"
    assert "नमस्ते".encode() in body  # not \u-escaped
    data = json.loads(body)
    assert data["processed_at"] == "2026-10-15T14:32:10+05:30"
    assert data["segments"][0]["speaker"] == "Speaker 1"
    assert data["segments"][1]["english_text"] is None
    for key in ("call_id", "source_file", "ticket_id", "agent_name", "duration_seconds", "source_language",
                "target_language", "status", "segments", "full_text_original", "full_text_english"):  # fmt: skip
        assert key in data


def test_calls_csv_is_one_row_per_call():
    body, _, name = export(record(), "calls_csv")
    assert name == "c7f3a2e1_calls.csv"
    rows = read_csv(body)
    assert len(rows) == 1
    assert list(rows[0]) == CALLS_COLUMNS
    assert rows[0]["transcript_english"] == "line one\nline two"  # multi-line cell survives
    assert rows[0]["status"] == "partial"


def test_segments_csv_rows_and_escaping():
    body, _, _ = export(record(), "segments_csv")
    rows = read_csv(body)
    assert [r["segment_no"] for r in rows] == ["1", "2", "3"]
    assert list(rows[0]) == SEGMENTS_COLUMNS
    assert rows[0]["original_text"] == "नमस्ते, मेरा इंटरनेट दो दिन से बंद है।"
    assert rows[1]["original_text"] == "'=SUM(A1)", "formula-like text must not run in a spreadsheet"
    assert rows[1]["english_text"] == "" and rows[1]["speaker"] == ""
    assert rows[2]["original_text"] == 'வணக்கம், "சரி", ok'


def analysed() -> CallRecord:
    r = record()
    r.analysis = CallAnalysis(
        summary="Internet down; agent raised a ticket.",
        customer_issue="Internet down for two days.",
        resolution=Resolution.unresolved,
        action_items=["Technician visit", "Call back on Monday"],
        topics=["internet", "outage"],
        overall_sentiment=Sentiment.negative,
        sentiment_score=-0.6,
        customer_sentiment_trend=SentimentTrend.steady,
        sentiment_explanation="Frustrated throughout.",
        model="sarvam-105b",
    )
    return r


def test_analysis_in_json_and_csv():
    data = json.loads(export(analysed(), "json")[0])
    assert data["analysis"]["resolution"] == "unresolved" and data["segments"][0]["sentiment"] == "negative"
    row = read_csv(export(analysed(), "calls_csv")[0])[0]
    assert row["summary"] == "Internet down; agent raised a ticket."
    assert row["action_items"] == "Technician visit\nCall back on Monday"
    assert row["topics"] == "internet; outage"
    assert row["overall_sentiment"] == "negative" and row["sentiment_score"] == "-0.6"
    segs = read_csv(export(analysed(), "segments_csv")[0])
    assert [s["sentiment"] for s in segs] == ["negative", "", ""]


def test_calls_csv_without_analysis_has_empty_analysis_cells():
    row = read_csv(export(record(), "calls_csv")[0])[0]
    assert row["summary"] == "" and row["overall_sentiment"] == ""
