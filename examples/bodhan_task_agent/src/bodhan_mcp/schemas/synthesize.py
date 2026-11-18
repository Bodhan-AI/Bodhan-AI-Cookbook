from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bodhan_mcp.core.languages import SPEAK_VOICES, SpeakLanguage, SpeakStyle, SpeakVoice
from bodhan_mcp.schemas.base import ToolInput, ToolOutput

SpeechFormat = Literal["wav", "mp3"]
VoiceGender = Literal["female", "male"]
VerificationStatus = Literal["passed", "warning", "skipped", "error"]

AI_SPEECH_LABEL = "AI-generated speech (Bodhan Indic-Speak)"


@dataclass(frozen=True)
class SynthesizeResult:
    audio: bytes
    request_id: str | None = None


class SynthesizeInput(ToolInput):
    text: str | None = Field(
        default=None,
        description="Text to read aloud, already in the target language. Give either text or text_path, not both.",
    )
    text_path: str | None = Field(
        default=None,
        description=(
            "Path to a UTF-8 text or Markdown file to read aloud. Relative paths are resolved against the "
            "server's input folder. Markdown symbols are removed before speaking."
        ),
    )
    language: SpeakLanguage = Field(
        description=(
            "Language the text is written in. This tool does not translate: translate first if the text is in "
            "another language."
        ),
    )
    voice: SpeakVoice | None = Field(
        default=None,
        description=(
            "Exact voice name. Any voice can read any language, but each was recorded in one language and sounds "
            "most natural in it. Leave empty to pick by gender."
        ),
    )
    gender: VoiceGender | None = Field(
        default=None,
        description=(
            "Pick the default voice of this gender for the language. Used when voice is empty; if both are given "
            "they must agree. Defaults to female when neither is given."
        ),
    )
    style: SpeakStyle | None = Field(
        default=None,
        description="Delivery style. Leave empty for the voice's neutral reading.",
    )
    format: SpeechFormat = Field(
        default="wav",
        description="Output audio format. Bodhan returns 24 kHz mono WAV; mp3 is converted locally.",
    )
    output_path: str | None = Field(
        default=None,
        description=(
            "Where to write the audio, inside the server's output folder. Its extension must match format. "
            "Defaults to a name derived from the input."
        ),
    )
    overwrite: bool = Field(default=False, description="Replace output_path if it already exists.")
    verify: bool = Field(
        default=True,
        description=(
            "Back-transcribe the generated audio with Bodhan Indic-Transcribe and compare it with the text. Adds "
            "model calls, time and cost. Measures text agreement, not naturalness or pronunciation."
        ),
    )

    @model_validator(mode="after")
    def _check(self) -> SynthesizeInput:
        if (self.text is None) == (self.text_path is None):
            raise ValueError("Provide exactly one of text or text_path.")
        if self.text is not None and not self.text.strip():
            raise ValueError("text must contain non-whitespace characters.")
        if self.voice is not None and self.gender is not None and SPEAK_VOICES[self.voice][1] != self.gender:
            raise ValueError(f"voice '{self.voice}' is {SPEAK_VOICES[self.voice][1]}, not {self.gender}.")
        if self.output_path is not None:
            if Path(self.output_path.strip()).suffix.lower() != f".{self.format}":
                raise ValueError(f"output_path must end in .{self.format} to match format.")
        return self


class SpeechVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: VerificationStatus = Field(
        description=(
            "'passed': the back-transcription matches the text closely. 'warning': text may be missing or "
            "repeated; see reason. 'skipped': verification was not requested. 'error': verification could not "
            "run; the audio itself was still produced."
        ),
    )
    word_recall: float | None = Field(
        default=None,
        ge=0,
        le=1,
        allow_inf_nan=False,
        description=(
            "Share of the text's words found in the back-transcription. Measures text agreement, not speech "
            "quality; numbers, dates and abbreviations can lower it even when the audio is right."
        ),
    )
    potentially_unmatched: list[str] = Field(
        default_factory=list,
        description="Stretches of the text not found in the back-transcription. Not proof they are missing.",
    )
    reason: str | None = None


class SynthesizeOutput(ToolOutput):
    output_path: str = Field(description="The audio file. Its metadata carries the AI-generated label.")
    format: SpeechFormat
    label: str = Field(
        default=AI_SPEECH_LABEL,
        description="Disclosure identifying the audio as AI-generated; show or keep it wherever the audio is shared.",
    )
    voice: str = Field(description="Voice actually used.")
    voice_gender: VoiceGender
    language: str
    style: str | None
    duration_s: float = Field(ge=0, allow_inf_nan=False, description="Length of the final audio in seconds.")
    chars: int = Field(ge=0, description="Characters sent for speech after Markdown cleanup; cost is based on this.")
    chunks: int = Field(ge=0, description="Pieces the text was split into; Bodhan speaks about 30 s per request.")
    verification: SpeechVerification
    warnings: list[str] = Field(default_factory=list)
