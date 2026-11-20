from __future__ import annotations

import logging
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from bodhan_mcp.config import CostLedger, Settings
from bodhan_mcp.tools import ToolService, safe_tool_result

TranscriptionLanguage = Literal[
    "as", "mai", "ml", "mni", "bn", "mr", "brx", "ne", "doi", "or", "en",
    "pa", "gu", "sa", "hi", "sat", "kn", "sd", "kok", "ta", "ks", "te", "ur", "bhb", "bho",
]
TranslationLanguage = Literal[
    "as", "kok", "or", "ks", "pa", "brx", "ks-Deva", "sa", "doi", "mai", "sat",
    "sd", "gu", "mni", "sd-Arab", "hi", "mni-Beng", "ta", "kn", "mr", "te", "ne", "ur", "bn", "ml", "en",
]
SpeechLanguage = Literal[
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "mai", "ml",
    "mni", "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur",
]
SpeechScript = Literal["native", "roman", "codemix"]
SpeechFormat = Literal["wav", "mp3"]
SpeechVoice = Literal[
    "Prastuti", "Ankur", "Ishita", "Sourav", "Gwrbw", "Sansuma", "Preeti", "Sham",
    "Dhara", "Parth", "Kavya", "Suhani", "Amit", "Deepika", "Adarsh", "Anjali",
    "Sandeep", "Zoon", "Ishfaq", "Vaidehi", "Madhukar", "Lakshmi", "Kiran",
    "Thoibi", "Chaoba", "Anagha", "Chinmay", "Srijana", "Sagar", "Itishree",
    "Akash", "Kaur", "Manpreet", "Bharati", "Aryaman", "Phulmani", "Sibu",
    "Moomal", "Rano", "Anitha", "Arun", "Sravani", "Vamsi", "Saba", "Zaid",
]
SpeechStyle = Literal[
    "AIR style news", "Customer Care", "TV style news", "advertisements", "anger",
    "children's stories", "disgust", "educational lecture", "fear", "happy", "news",
    "sad", "single person narration audiobook", "surprise",
]


class ToolErrorOutput(BaseModel):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class _OutputData(BaseModel):
    model_config = {"extra": "allow"}


class TranscriptionData(_OutputData):
    transcript: str | None = None
    detected_language: dict[str, Any] | None = None
    source_duration_seconds: float | None = None
    chunks: int | None = None
    model: str | None = None
    input_fingerprint: str | None = None
    usage: dict[str, float | int] | None = None
    retries: int | None = None
    artifact_path: str | None = None
    segments_artifact_path: str | None = None
    verification: dict[str, Any] | None = None


class TranslationData(_OutputData):
    translated_text: str | None = None
    target_language: str | None = None
    script: str | None = None
    chunks: int | None = None
    coverage: str | None = None
    model: str | None = None
    input_fingerprint: str | None = None
    usage: dict[str, float | int] | None = None
    cost_basis: str | None = None
    verification: dict[str, Any] | None = None
    artifact_path: str | None = None


class SubtitleData(_OutputData):
    target_language: str | None = None
    translated_segments: int | None = None
    source_segments: int | None = None
    coverage: str | None = None
    artifact_path: str | None = None


class SpeechData(_OutputData):
    artifact_path: str | None = None
    ai_generated: bool | None = None
    duration_seconds: float | None = None
    language: str | None = None
    voice: str | None = None
    input_characters: int | None = None
    model: str | None = None
    input_fingerprint: str | None = None
    verification: dict[str, Any] | None = None
    output_format: str | None = None
    wav_artifact_path: str | None = None


class OCRData(_OutputData):
    page_count: int | None = None
    processed_pages: list[int] | None = None
    page_results: list[dict[str, Any]] | None = None
    text: str | None = None
    table_format: str | None = None
    model: str | None = None
    input_fingerprint: str | None = None
    usage: dict[str, float | int] | None = None
    verification: dict[str, Any] | None = None
    artifact_path: str | None = None


class BaseToolOutput(BaseModel):
    ok: bool
    tool: str
    data: dict[str, Any] | None = None
    cost_inr: float = Field(
        default=0.0,
        description="Compatibility alias for estimated_cost_inr; this is not billed actual cost.",
    )
    estimated_cost_inr: float = Field(
        default=0.0,
        description="Estimated Bodhan cost in INR using assignment rates.",
    )
    actual_cost_inr: float | None = Field(
        default=None,
        description="Actual billed cost is unavailable unless reported by the provider.",
    )
    latency_ms: int | None = None
    warnings: list[str] = Field(default_factory=list)
    error: ToolErrorOutput | None = None


class TranscriptionOutput(BaseToolOutput):
    tool: Literal["transcribe_audio"]
    data: TranscriptionData | None = None


class TranslationOutput(BaseToolOutput):
    tool: Literal["translate_text"]
    data: TranslationData | None = None


class SubtitleOutput(BaseToolOutput):
    tool: Literal["translate_subtitles"]
    data: SubtitleData | None = None


class SpeechOutput(BaseToolOutput):
    tool: Literal["generate_speech"]
    data: SpeechData | None = None


class OCROutput(BaseToolOutput):
    tool: Literal["ocr_document"]
    data: OCRData | None = None


def _output(
    tool: str,
    result: dict[str, Any],
    output_model: type[BaseToolOutput],
) -> BaseToolOutput:
    if not result.get("ok"):
        metadata = {
            "ok", "tool", "cost_inr", "estimated_cost_inr", "actual_cost_inr",
            "latency_ms", "warnings", "error",
        }
        return output_model(
            ok=False,
            tool=tool,
            data={key: value for key, value in result.items() if key not in metadata} or None,
            cost_inr=float(result.get("cost_inr", 0)),
            estimated_cost_inr=float(result.get("estimated_cost_inr", result.get("cost_inr", 0))),
            actual_cost_inr=result.get("actual_cost_inr"),
            latency_ms=result.get("latency_ms"),
            warnings=result.get("warnings", []),
            error=result.get("error", {"code": "tool_error", "message": "Tool request failed."}),
        )
    metadata = {
        "ok", "tool", "cost_inr", "estimated_cost_inr", "actual_cost_inr",
        "latency_ms", "warnings",
    }
    return output_model(
        ok=True,
        tool=tool,
        data={key: value for key, value in result.items() if key not in metadata},
        cost_inr=float(result.get("cost_inr", 0)),
        estimated_cost_inr=float(result.get("estimated_cost_inr", result.get("cost_inr", 0))),
        actual_cost_inr=result.get("actual_cost_inr"),
        latency_ms=result.get("latency_ms"),
        warnings=result.get("warnings", []),
    )


def create_server(service: ToolService) -> FastMCP:
    server = FastMCP("Bodhan Indic Tools")

    @server.tool(structured_output=True)
    def transcribe_audio(
        file_path: str,
        language: TranscriptionLanguage | None = None,
        window_seconds: float = 30.0,
        include_segments: bool = False,
    ) -> TranscriptionOutput:
        """Transcribe WAV, FLAC, OGG, or MP3 audio with Bodhan Indic-Transcribe.

        Audio is split into windows of at most 30 seconds. Bodhan does not
        provide timestamps, so include_segments is compatibility-only and
        does not create SRT timing.
        """
        return _output(
            "transcribe_audio",
            safe_tool_result(
                service.transcribe_audio,
                file_path,
                language,
                window_seconds,
                include_segments,
            ),
            TranscriptionOutput,
        )

    @server.tool(structured_output=True)
    def translate_text(
        target_language: TranslationLanguage,
        text: str | None = None,
        script: SpeechScript = "native",
        text_file_path: str | None = None,
    ) -> TranslationOutput:
        """Translate text with Bodhan Indic-Translate and save a Markdown artifact.

        Use a Bodhan language code. `script` is native, roman, or codemix.
        Provide short non-Indic text inline or a UTF-8 .txt/.md/.srt/.csv file reference.
        For Indic text from another Bodhan tool, pass that tool's exact artifact_path as text_file_path.
        Long text is split internally; every chunk is translated in order.
        """
        return _output(
            "translate_text",
            safe_tool_result(service.translate_text, text, target_language, script, text_file_path),
            TranslationOutput,
        )

    @server.tool(structured_output=True)
    def translate_subtitles(
        segments_file_path: str,
        target_language: TranslationLanguage,
    ) -> SubtitleOutput:
        """Translate each caption in a local SRT file with Bodhan Indic-Translate.

        Preserves SRT caption numbering and time ranges; input must be a
        user-provided SRT with valid timestamps.
        """
        return _output(
            "translate_subtitles",
            safe_tool_result(service.translate_subtitles, segments_file_path, target_language),
            SubtitleOutput,
        )

    @server.tool(structured_output=True)
    def generate_speech(
        language: SpeechLanguage,
        text: str | None = None,
        voice: SpeechVoice | None = None,
        style: SpeechStyle | None = None,
        text_file_path: str | None = None,
        output_format: SpeechFormat = "wav",
    ) -> SpeechOutput:
        """Generate AI speech with Bodhan Indic-Speak and verify it by back-transcription.

        Language is a Bodhan language code. The optional fixed-library voice
        is one of Prastuti, Ankur, Ishita, Sourav, Gwrbw, Sansuma, Preeti,
        Sham, Dhara, Parth, Kavya, Suhani, Amit, Deepika, Adarsh, Anjali,
        Sandeep, Zoon, Ishfaq, Vaidehi, Madhukar, Lakshmi, Kiran, Thoibi,
        Chaoba, Anagha, Chinmay, Srijana, Sagar, Itishree, Akash, Kaur,
        Manpreet, Bharati, Aryaman, Phulmani, Sibu, Moomal, Rano, Anitha,
        Arun, Sravani, Vamsi, Saba, or Zaid. Any listed voice can read any
        supported language. Audio is back-transcribed with Bodhan
        Indic-Transcribe for verification.
        """
        return _output(
            "generate_speech",
            safe_tool_result(
                service.generate_speech,
                text,
                language,
                voice,
                style,
                text_file_path,
                output_format,
            ),
            SpeechOutput,
        )

    @server.tool(structured_output=True)
    def ocr_document(file_path: str, table_format: str = "html") -> OCROutput:
        """Extract text/layout with Bodhan Indic-OCR from a PDF or PNG/JPEG/WEBP.

        PDFs are rendered and sent one page at a time. `table_format` is
        html (best for merged cells) or markdown (simple pipe tables).
        """
        return _output(
            "ocr_document",
            safe_tool_result(service.ocr_document, file_path, table_format),
            OCROutput,
        )

    return server


def main() -> None:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    roots = (Path.cwd().resolve(),)
    budget = None
    budget_text = os.getenv("BODHAN_COST_BUDGET_INR")
    if budget_text is not None:
        try:
            maximum = Decimal(budget_text)
        except InvalidOperation as exc:
            raise SystemExit("BODHAN_COST_BUDGET_INR must be a finite positive INR amount.") from exc
        if not maximum.is_finite() or maximum <= 0:
            raise SystemExit("BODHAN_COST_BUDGET_INR must be a finite positive INR amount.")
        budget = CostLedger(maximum=maximum)
    service = ToolService(Settings.from_env(), input_roots=roots, ledger=budget)
    server = create_server(service)
    try:
        server.run(transport="stdio")
    finally:
        service.close()


if __name__ == "__main__":
    main()
