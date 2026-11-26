from typing import Any

from pydantic import BaseModel, Field, model_validator


class WordInfo(BaseModel):
    word: str
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    confidence: float | None = None

class DubSegment(BaseModel):
    id: str
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    speaker: str | None = "Speaker_1"

    source_text: str = ""
    condensed_text: str | None = None
    translated_text: str = ""

    source_language: str = "en"
    target_language: str = "hi"

    words: list[WordInfo] = Field(default_factory=list)
    confidence: float | None = None

    generated_audio_path: str | None = None
    fitted_audio_path: str | None = None

    original_audio_duration: float | None = None
    fitted_audio_duration: float | None = None
    applied_tempo_factor: float = 1.0

    # Back-transcription and QA metrics
    back_transcription: str | None = None
    cer: float | None = None
    levenshtein_distance: int | None = None
    qa_status: str = "PENDING"  # ACCEPTED, CONDENSED, RETRIED, MANUAL_REVIEW, REJECTED
    qa_reasons: list[str] = Field(default_factory=list)

    retry_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def target_duration(self) -> float:
        return max(0.0, self.end - self.start)

    @model_validator(mode="after")
    def validate_times(self):
        if self.end < self.start:
            raise ValueError(f"Segment {self.id}: end time {self.end} is less than start time {self.start}")
        return self
