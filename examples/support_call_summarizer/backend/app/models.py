"""Pydantic models shared by the API, the pipeline, the exporters and the CLI.

The JSON export is ``CallRecord.model_dump(mode="json")`` and both CSV files are
built from the same object, so there is exactly one definition of the output.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class CallStatus(StrEnum):
    queued = "queued"
    processing = "processing"
    completed = "completed"
    partial = "partial"  # finished, but at least one piece failed
    failed = "failed"


class Stage(StrEnum):
    queued = "queued"
    converting = "converting"
    diarizing = "diarizing"
    segmenting = "segmenting"
    transcribing = "transcribing"
    translating = "translating"
    analyzing = "analyzing"
    done = "done"


class Sentiment(StrEnum):
    positive = "positive"
    neutral = "neutral"
    negative = "negative"
    mixed = "mixed"  # only for the whole call, never for one segment


class Resolution(StrEnum):
    resolved = "resolved"
    partially_resolved = "partially_resolved"
    unresolved = "unresolved"
    unclear = "unclear"


class SentimentTrend(StrEnum):
    improved = "improved"
    steady = "steady"
    worsened = "worsened"


class Segment(BaseModel):
    segment_no: int
    start: float = Field(description="seconds from the start of the call")
    end: float
    speaker: str | None = Field(default=None, description="'Speaker 1'..'Speaker 8' when diarization is on")
    original_text: str = ""
    english_text: str | None = Field(
        default=None, description="null when translation failed or the language has no translation"
    )
    error: str | None = Field(default=None, description="why this piece is incomplete, if it is")
    sentiment: Sentiment | None = Field(default=None, description="positive/neutral/negative; null without analysis")


class CallAnalysis(BaseModel):
    """What the Sarvam LLM concluded about the whole call (see ``analysis.py``)."""

    summary: str = Field(description="a short English summary of the call")
    customer_issue: str = Field(description="the customer's problem or request in one sentence")
    resolution: Resolution
    action_items: list[str] = Field(default_factory=list, description="follow-ups promised or needed")
    topics: list[str] = Field(default_factory=list)
    overall_sentiment: Sentiment
    sentiment_score: float = Field(ge=-1, le=1, description="-1 very negative .. 1 very positive")
    customer_sentiment_trend: SentimentTrend = Field(description="how the customer's mood changed during the call")
    sentiment_explanation: str = Field(description="why the call got this sentiment")
    model: str = Field(description="the LLM that produced this analysis")


class CallRecord(BaseModel):
    call_id: str
    source_file: str
    ticket_id: str | None = None
    agent_name: str | None = None
    duration_seconds: float
    source_language: str | None = Field(default=None, description="null when the user did not select it")
    target_language: str = "en"
    translation_available: bool = True
    processed_at: datetime
    status: CallStatus
    speaker_count: int | None = Field(default=None, description="null when diarization was not run")
    segments: list[Segment] = Field(default_factory=list)
    full_text_original: str = ""
    full_text_english: str = ""
    analysis: CallAnalysis | None = Field(default=None, description="null when analysis is off or failed")
    warnings: list[str] = Field(default_factory=list)


class Progress(BaseModel):
    stage: Stage = Stage.queued
    done: int = 0
    total: int = 0
    message: str = "Waiting to start"


class JobError(BaseModel):
    code: str
    message: str


class JobView(BaseModel):
    """Response of ``GET /api/calls/{call_id}``; the app polls this."""

    call_id: str
    status: CallStatus
    progress: Progress
    error: JobError | None = None
    record: CallRecord | None = None
    created_at: datetime
    expires_at: datetime


class CreateCallResponse(BaseModel):
    call_id: str
    status: CallStatus


class Language(BaseModel):
    code: str
    name: str
    translation: bool = Field(description="false for Bhili and Bhojpuri: transcript only")


class LanguagesResponse(BaseModel):
    languages: list[Language]


class AppConfig(BaseModel):
    """Limits the app shows and checks before uploading (the server checks again)."""

    max_file_mb: int
    max_duration_seconds: int
    allowed_extensions: list[str]
    diarization_enabled: bool
    analysis_enabled: bool
    max_speakers: int
    result_ttl_seconds: int


class ErrorResponse(BaseModel):
    error: JobError
