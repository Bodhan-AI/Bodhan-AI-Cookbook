from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bodhan_mcp.core.languages import TranscribeLanguage
from bodhan_mcp.schemas.base import UNTRUSTED_CONTENT_DESCRIPTION, ToolInput, ToolOutput

LanguageSource = Literal["requested", "detected", "undetermined"]
SegmentStatus = Literal["transcribed", "silent", "suspect"]

INLINE_SEGMENT_LIMIT = 10


@dataclass(frozen=True)
class TranscribeResult:
    text: str
    request_id: str | None = None


class TranscribeInput(ToolInput):
    audio_path: str = Field(
        description=(
            "Path to the audio or video file to transcribe (WAV, MP3, M4A, FLAC, OGG, OPUS, WEBM, MP4 and similar). "
            "Relative paths are resolved against the server's input folder. Long audio is split at pauses "
            "automatically."
        ),
    )
    language: TranscribeLanguage | None = Field(
        default=None,
        description=(
            "Spoken language, only if known. Leave empty to let the model choose. A wrong value degrades the "
            "transcript. Bhojpuri, Maithili and Urdu are often transcribed as Hindi unless given; Urdu is written "
            "in Perso-Arabic script only when 'ur' is given."
        ),
    )
    vad: bool = Field(
        default=False,
        description="Ask Bodhan to apply voice-activity detection. Slower; leave off unless the audio is very noisy.",
    )
    output_path: str | None = Field(
        default=None,
        description=(
            "Where to write the full transcript, inside the server's output folder. Segments are written next to "
            "it as JSON. Defaults to a name derived from the input."
        ),
    )
    overwrite: bool = Field(default=False, description="Replace output_path if it already exists.")

    @model_validator(mode="after")
    def _check(self) -> TranscribeInput:
        if not self.audio_path.strip():
            raise ValueError("audio_path must not be empty.")
        return self


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_s: float = Field(ge=0, allow_inf_nan=False, description="Start of the audio processing window.")
    end_s: float = Field(ge=0, allow_inf_nan=False, description="End of the audio processing window.")
    text: str
    status: SegmentStatus = Field(
        description=(
            "'transcribed': normal model output. 'silent': window was mostly silence and deliberately not sent to "
            "the model; text is empty. 'suspect': output flagged by heuristic checks (heavy repetition, an "
            "unexpected script, or far less text than the audio length suggests); text is kept but should be "
            "verified. Does not prove the text is wrong."
        ),
    )

    @model_validator(mode="after")
    def _check(self) -> TranscriptSegment:
        if self.end_s <= self.start_s:
            raise ValueError("end_s must be after start_s.")
        return self


class TranscribeOutput(ToolOutput):
    output_path: str = Field(description="File holding the complete transcript.")
    segments_path: str = Field(
        description=(
            "JSON file with every segment's text and audio window. Windows are cut at pauses and last up to "
            "25 s; they are processing windows, not sentence- or word-aligned subtitle timings."
        ),
    )
    transcript: str = Field(description="The transcript, or its first part when transcript_is_preview is true.")
    transcript_is_preview: bool = Field(description="True when transcript is truncated; read output_path for all.")
    language: str | None = Field(
        description="Language code: the one requested, or one detected from the transcript's script. Null otherwise.",
    )
    language_source: LanguageSource = Field(
        description=(
            "'requested': given by the caller. 'detected': the transcript's script is used by only one supported "
            "language (for example Telugu or Tamil script). 'undetermined': the script is shared by several "
            "languages (Devanagari, Perso-Arabic, Bengali) or no text was produced; see candidate_languages."
        ),
    )
    script: str | None = Field(description="Main writing system of the transcript, such as 'Devanagari' or 'Telugu'.")
    candidate_languages: list[str] = Field(
        default_factory=list,
        description=(
            "Supported languages written in the detected script, when language_source is 'undetermined'. "
            "Not a ranked detection result."
        ),
    )
    duration_s: float = Field(ge=0, allow_inf_nan=False, description="Length of the input audio in seconds.")
    chunks: int = Field(ge=0, description="Windows the audio was split into.")
    silent_chunks: int = Field(ge=0, description="Windows skipped because they were mostly silence.")
    suspect_chunks: int = Field(ge=0, description="Windows whose text was flagged by heuristic checks.")
    segments: list[TranscriptSegment] | None = Field(
        default=None,
        description=f"All segments when there are at most {INLINE_SEGMENT_LIMIT}; otherwise null, read segments_path.",
    )
    verification_status: Literal["passed", "warning"] = Field(
        description=(
            "Structural checks only: every window transcribed or deliberately skipped, no suspect segments, "
            "script consistent with the requested language. Not a check of transcription accuracy."
        ),
    )
    untrusted_content: Literal[True] = Field(default=True, description=UNTRUSTED_CONTENT_DESCRIPTION)
    warnings: list[str] = Field(default_factory=list)
