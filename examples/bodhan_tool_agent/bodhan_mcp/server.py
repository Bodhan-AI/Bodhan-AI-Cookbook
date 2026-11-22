
from __future__ import annotations

import io
import wave
from pathlib import Path
from typing import Any, Literal

from bodhan_tool_agent.audio import (
    AudioProcessingError,
    audio_duration_seconds,
    chunk_audio_file,
)
from bodhan_tool_agent.bodhan_client import (
    DEFAULT_TRANSLATION_MAX_OUTPUT_TOKENS,
    BodhanAPIError,
    BodhanClient,
    BodhanDeadlineExceeded,
    BodhanNetworkError,
    BodhanResponseError,
    BodhanUncertainOutcomeError,
    TranslationChunkError,
    TranslationTruncatedError,
)
from bodhan_tool_agent.config import ConfigurationError, Settings
from bodhan_tool_agent.pdf import (
    PDFProcessingError,
    pdf_page_count,
    render_pdf_pages,
)
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field, ValidationError

RECIPE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = RECIPE_ROOT / "outputs"
AUDIO_EXTENSIONS = {".wav", ".flac", ".ogg", ".mp3"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}


class TranscriptionOutput(BaseModel):
    text: str
    file_path: str
    language: str | None
    duration_seconds: float | None
    chunk_count: int
    chunk_durations_seconds: list[float]
    model: Literal["indic-transcribe"] = "indic-transcribe"
    retry_attempts: int = 0
    retry_succeeded: bool = False
    retry_exhausted: bool = False
    uncertain_paid_outcome: bool = False


class TranslationOutput(BaseModel):
    translation: str
    status: Literal["success", "failed", "truncated"] = "success"
    target_language: str
    script: str | None
    truncated: bool
    usage: dict[str, Any]
    partial: bool = False
    partial_translation: str = ""
    chunk_count: int = 1
    completed_chunk_count: int = 0
    failed_chunk_index: int | None = None
    chunk_character_counts: list[int] = Field(default_factory=list)
    chunk_usage: list[dict[str, Any]] = Field(default_factory=list)
    retry_attempts: int = 0
    retry_succeeded: bool = False
    retry_exhausted: bool = False
    uncertain_paid_outcome: bool = False


class SpeechOutput(BaseModel):
    output_path: str
    content_type: Literal["audio/wav"] = "audio/wav"
    size_bytes: int
    retry_attempts: int = 0
    retry_succeeded: bool = False
    retry_exhausted: bool = False
    uncertain_paid_outcome: bool = False


class OCROutput(BaseModel):
    text: str
    blocks: list[dict[str, Any]]
    usage: dict[str, Any]
    status: Literal["success", "failed"] = "success"
    partial: bool = False
    completed_pages: int = 0
    total_pages: int = 1
    failed_page: int | None = None
    page_text: list[str] = Field(default_factory=list)
    page_usage: list[dict[str, Any]] = Field(default_factory=list)
    retry_attempts: int = 0
    retry_succeeded: bool = False
    retry_exhausted: bool = False
    uncertain_paid_outcome: bool = False


def _validate_wav(audio_bytes: bytes) -> None:
    """Validate Bodhan's expected mono, 16-bit, 24 kHz PCM WAV output."""
    if not isinstance(audio_bytes, bytes) or not audio_bytes:
        raise ToolError("Bodhan returned empty or invalid audio data")

    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
            channels = wav_file.getnchannels()
            sample_width = wav_file.getsampwidth()
            sample_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            compression = wav_file.getcomptype()

            if channels != 1:
                raise ToolError("Bodhan WAV must contain mono audio")

            if sample_width != 2:
                raise ToolError("Bodhan WAV must use 16-bit PCM audio")

            if sample_rate != 24000:
                raise ToolError(
                    "Bodhan WAV must use a 24000 Hz sample rate"
                )

            if compression != "NONE":
                raise ToolError("Bodhan WAV must use uncompressed PCM audio")

            if frame_count <= 0:
                raise ToolError("Bodhan WAV contains no audio frames")

            frames = wav_file.readframes(frame_count)
            expected_bytes = frame_count * channels * sample_width

            if len(frames) != expected_bytes:
                raise ToolError("Bodhan returned incomplete WAV audio")

    except ToolError:
        raise
    except (wave.Error, EOFError, OSError):
        raise ToolError("Bodhan returned malformed WAV audio") from None


def _validate_input_file(
    file_path: str,
    extensions: set[str],
    label: str,
) -> Path:
    if not file_path.strip():
        raise ToolError(f"{label} path must not be empty")

    path = Path(file_path).expanduser()

    if path.suffix and path.suffix.lower() not in extensions:
        allowed = ", ".join(sorted(extensions))
        raise ToolError(
            f"Unsupported {label.lower()} file extension; "
            f"supported extensions: {allowed}"
        )

    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ToolError(
            f"{label} file does not exist or cannot be resolved"
        ) from None

    if not resolved.is_file():
        raise ToolError(f"{label} path must point to a file")

    try:
        with resolved.open("rb") as media_file:
            media_file.read(0)
    except OSError:
        raise ToolError(f"{label} file is not readable") from None

    return resolved


def _output_path(path_value: str, output_dir: Path) -> Path:
    if not path_value.strip():
        raise ToolError("output_path must not be empty")

    output_dir = output_dir.resolve()
    candidate = Path(path_value).expanduser()

    if not candidate.is_absolute():
        candidate = output_dir / candidate

    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        raise ToolError("output_path cannot be resolved") from None

    if not resolved.is_relative_to(output_dir):
        raise ToolError(
            "output_path must stay inside the recipe outputs directory"
        )

    if resolved.suffix.lower() != ".wav":
        raise ToolError("output_path must use the .wav extension")

    if resolved.exists():
        raise ToolError(
            "output_path already exists; choose a new output filename"
        )

    return resolved


def _aggregate_usage(
    usage_items: list[dict[str, Any]],
) -> dict[str, Any]:
    aggregate: dict[str, Any] = {}

    for usage in usage_items:
        if not isinstance(usage, dict):
            continue

        for key, value in usage.items():
            if isinstance(value, bool):
                continue

            if isinstance(value, (int, float)):
                current = aggregate.get(key)

                if (
                    isinstance(current, (int, float))
                    and not isinstance(current, bool)
                ):
                    aggregate[key] = current + value
                elif key not in aggregate:
                    aggregate[key] = value

    return aggregate


def _retry_metadata(client: BodhanClient) -> dict[str, Any]:
    stats = getattr(client, "last_retry_stats", {})

    if not isinstance(stats, dict):
        return {
            "retry_attempts": 0,
            "retry_succeeded": False,
            "retry_exhausted": False,
            "uncertain_paid_outcome": False,
        }

    return {
        "retry_attempts": int(stats.get("retry_attempts", 0) or 0),
        "retry_succeeded": bool(stats.get("retry_succeeded", False)),
        "retry_exhausted": bool(stats.get("retry_exhausted", False)),
        "uncertain_paid_outcome": bool(
            stats.get("uncertain_paid_outcome", False)
        ),
    }


def _tool_error(operation: str, error: Exception) -> ToolError:
    if isinstance(error, BodhanUncertainOutcomeError):
        return ToolError(
            f"{operation} outcome is uncertain: a paid request timed out "
            "after the provider may have processed it"
        )

    if isinstance(error, BodhanDeadlineExceeded):
        return ToolError(
            f"{operation} stopped because the task deadline expired"
        )

    if isinstance(error, PDFProcessingError):
        return ToolError(f"{operation} failed: {error}")

    if isinstance(error, TranslationChunkError):
        return ToolError(
            f"{operation} partially completed. "
            f"Failed at chunk {error.chunk_index} of {error.chunk_count}; "
            f"partial translation: {error.partial_translation!r}; "
            "The full translation is incomplete."
        )

    if isinstance(error, TranslationTruncatedError):
        return ToolError(
            "Translation is incomplete (truncated=true). "
            f"Partial translation: {error.translation!r}; "
            f"usage: {error.usage!r}. "
            "Retry with shorter input or a larger output budget."
        )

    if isinstance(error, BodhanAPIError):
        detail = f"HTTP {error.status_code}"

        if error.code:
            detail += f", code={error.code}"

        if error.request_id:
            detail += f", request_id={error.request_id}"

        return ToolError(
            f"{operation} failed: Bodhan returned {detail}"
        )

    if isinstance(error, BodhanNetworkError):
        return ToolError(
            f"{operation} failed: network retries were exhausted"
        )

    if isinstance(error, BodhanResponseError):
        return ToolError(
            f"{operation} failed: Bodhan returned an invalid response"
        )

    if isinstance(error, ValidationError):
        return ToolError(
            f"{operation} failed: Bodhan returned an invalid response. "
            "Please retry or check the request."
        )

    if isinstance(error, ConfigurationError):
        return ToolError(
            f"{operation} is not configured: {error}"
        )

    if isinstance(error, AudioProcessingError):
        return ToolError(
            f"{operation} failed: {error}"
        )

    if isinstance(error, OSError):
        return ToolError(
            f"{operation} could not access the requested file"
        )

    if isinstance(error, ValueError):
        return ToolError(
            f"{operation} input is invalid: {error}"
        )

    return ToolError(f"{operation} failed")


def create_server(
    client: BodhanClient | None = None,
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> MCPServer:
    """Create the MCP server, optionally injecting a client for tests."""
    if client is None:
        client = BodhanClient(Settings.from_env())

    output_root = Path(output_dir)

    server = MCPServer(
        name="Bodhan Indic Tools",
        instructions=(
            "Use the Bodhan Indic tools for transcription, translation, speech "
            "synthesis, and OCR. File inputs must be local paths. "
            "Audio transcription requests should contain approximately 30 "
            "seconds or less of audio. Speech output is written under the "
            "recipe outputs directory; tool results never include raw media bytes."
        ),
    )

    @server.tool()
    def indic_transcribe(
        file_path: str = Field(
            description="Path to a local WAV, FLAC, OGG, or MP3 audio file. "
            "Keep each request to approximately 30 seconds of audio."
        ),
        language: str | None = Field(
            default=None,
            description="Optional ISO language code for the audio, such as hi or ta.",
        ),
    ) -> TranscriptionOutput:
        """Transcribe a local audio file using Bodhan Indic-Transcribe."""
        try:
            path = _validate_input_file(
                file_path,
                AUDIO_EXTENSIONS,
                "Audio",
            )
            duration_seconds = audio_duration_seconds(path)
            transcripts: list[str] = []
            chunk_durations: list[float] = []
            retry_attempts = 0
            retry_succeeded = False
            retry_exhausted = False
            uncertain_paid_outcome = False

            with chunk_audio_file(path) as chunks:
                for chunk in chunks:
                    transcript = client.transcribe(chunk.path, language)

                    if not isinstance(transcript, str) or not transcript.strip():
                        raise BodhanResponseError(
                            "Bodhan transcription chunk returned empty text"
                        )

                    transcripts.append(transcript.strip())
                    chunk_durations.append(chunk.duration_seconds)

                    stats = _retry_metadata(client)
                    retry_attempts += stats["retry_attempts"]
                    retry_succeeded = (
                        retry_succeeded or stats["retry_succeeded"]
                    )
                    retry_exhausted = (
                        retry_exhausted or stats["retry_exhausted"]
                    )
                    uncertain_paid_outcome = (
                        uncertain_paid_outcome
                        or stats["uncertain_paid_outcome"]
                    )

            return TranscriptionOutput(
                text=" ".join(transcripts),
                file_path=str(path),
                language=language,
                duration_seconds=duration_seconds,
                chunk_count=len(transcripts),
                chunk_durations_seconds=chunk_durations,
                retry_attempts=retry_attempts,
                retry_succeeded=retry_succeeded,
                retry_exhausted=retry_exhausted,
                uncertain_paid_outcome=uncertain_paid_outcome,
            )

        except (
            ToolError,
            PDFProcessingError,
            AudioProcessingError,
            BodhanDeadlineExceeded,
            BodhanAPIError,
            BodhanNetworkError,
            BodhanResponseError,
            ConfigurationError,
            OSError,
            ValueError,
            ValidationError,
        ) as exc:
            if isinstance(exc, ToolError):
                raise

            raise _tool_error("Transcription", exc) from None

    @server.tool()
    def indic_translate(
        text: str = Field(
            min_length=1,
            description="Text to translate.",
        ),
        target_language: str = Field(
            min_length=1,
            description="Target language ISO code.",
        ),
        script: Literal["native", "roman", "codemix"] | None = Field(
            default=None,
            description="Optional output script: native, roman, or codemix.",
        ),
        max_output_tokens: int | None = Field(
            default=DEFAULT_TRANSLATION_MAX_OUTPUT_TOKENS,
            gt=0,
            description="Optional positive maximum output token budget.",
        ),
    ) -> TranslationOutput:
        """Translate text with Bodhan Indic-Translate and report usage and completeness."""
        try:
            effective_max_output_tokens = (
                DEFAULT_TRANSLATION_MAX_OUTPUT_TOKENS
                if max_output_tokens is None
                else max_output_tokens
            )

            result = client.translate(
                text,
                target_language,
                script=script,
                max_output_tokens=effective_max_output_tokens,
            )

            return TranslationOutput(
                translation=result["translation"],
                status="success",
                target_language=result.get(
                    "target_language",
                    target_language,
                ),
                script=result.get("script"),
                truncated=result["truncated"],
                usage=result["usage"],
                partial=result.get("partial", False),
                chunk_count=result.get("chunk_count", 1),
                chunk_character_counts=result.get(
                    "chunk_character_counts",
                    [],
                ),
                chunk_usage=result.get("chunk_usage", []),
                **_retry_metadata(client),
            )

        except TranslationChunkError as exc:
            return TranslationOutput(
                translation="",
                status="failed",
                target_language=target_language,
                script=script,
                truncated=False,
                usage=_aggregate_usage(exc.usage),
                partial=True,
                partial_translation=exc.partial_translation,
                chunk_count=exc.chunk_count,
                completed_chunk_count=exc.completed_chunk_count,
                failed_chunk_index=exc.chunk_index,
                chunk_usage=exc.usage,
                uncertain_paid_outcome=(
                    isinstance(
                        getattr(exc, "cause", None),
                        BodhanUncertainOutcomeError,
                    )
                ),
            )

        except TranslationTruncatedError as exc:
            return TranslationOutput(
                translation="",
                status="truncated",
                target_language=target_language,
                script=script,
                truncated=True,
                usage=exc.usage if isinstance(exc.usage, dict) else {},
                partial=True,
                partial_translation=exc.partial_translation or "",
                chunk_count=exc.chunk_count or 1,
                completed_chunk_count=exc.completed_chunk_count or 0,
                failed_chunk_index=exc.chunk_index,
            )

        except (
            BodhanDeadlineExceeded,
            BodhanAPIError,
            BodhanNetworkError,
            BodhanResponseError,
            ConfigurationError,
            OSError,
            ValueError,
            ValidationError,
        ) as exc:
            raise _tool_error("Translation", exc) from None

    @server.tool()
    def indic_speak(
        text: str = Field(
            min_length=1,
            description="Text to synthesize as speech.",
        ),
        language: str = Field(
            min_length=1,
            description="ISO language code for the text.",
        ),
        voice: str = Field(
            min_length=1,
            description="Bodhan voice name to use.",
        ),
        output_path: str = Field(
            description="New .wav output path inside the recipe outputs directory."
        ),
    ) -> SpeechOutput:
        """Generate speech and save the WAV file locally without returning media bytes."""
        try:
            output = _output_path(output_path, output_root)
            audio_bytes = client.speak(text, voice, language)

            # Validate the complete audio response before creating the file.
            _validate_wav(audio_bytes)

            output.parent.mkdir(parents=True, exist_ok=True)

            with output.open("xb") as wav_file:
                written = wav_file.write(audio_bytes)

            if written != len(audio_bytes):
                output.unlink(missing_ok=True)
                raise ToolError("Speech output file was not completely written")

            actual_size = output.stat().st_size

            if actual_size != len(audio_bytes) or actual_size <= 0:
                output.unlink(missing_ok=True)
                raise ToolError("Speech output file failed verification")

            return SpeechOutput(
                output_path=str(output),
                size_bytes=actual_size,
                **_retry_metadata(client),
            )

        except (
            ToolError,
            BodhanDeadlineExceeded,
            BodhanAPIError,
            BodhanNetworkError,
            BodhanResponseError,
            ConfigurationError,
            OSError,
            ValueError,
            ValidationError,
        ) as exc:
            if isinstance(exc, ToolError):
                raise

            raise _tool_error("Speech synthesis", exc) from None

    @server.tool()
    def indic_ocr(
        image_path: str = Field(
            description=(
                "Path to a local PNG/JPEG image or PDF. PDFs are limited to "
                "the configured page maximum (50 by default, configurable with "
                "BODHAN_MAX_PDF_PAGES) and are rendered internally."
            )
        ),
        table_format: Literal["html", "markdown"] | None = Field(
            default=None,
            description="Optional table output format supported by Bodhan OCR.",
        ),
        max_tokens: int | None = Field(
            default=None,
            gt=0,
            description="Optional OCR output limit per block.",
        ),
    ) -> OCROutput:
        """Extract text from a PNG/JPEG image or every page of a PDF."""
        try:
            path = _validate_input_file(
                image_path,
                IMAGE_EXTENSIONS | {".pdf"},
                "Image",
            )

            if path.suffix.lower() == ".pdf":
                total_pages = pdf_page_count(path)
                completed_text: list[str] = []
                completed_blocks: list[dict[str, Any]] = []
                available_usage: dict[str, Any] = {}
                page_usage: list[dict[str, Any]] = []
                retry_attempts = 0
                retry_succeeded = False
                retry_exhausted = False
                uncertain_paid_outcome = False

                with render_pdf_pages(path) as pages:
                    for page in pages:
                        try:
                            result = client.ocr(
                                page.path,
                                table_format=table_format,
                                max_tokens=max_tokens,
                            )
                        except Exception as exc:
                            if isinstance(exc, BodhanUncertainOutcomeError):
                                uncertain_paid_outcome = True

                            return OCROutput(
                                text="",
                                blocks=completed_blocks,
                                usage=available_usage,
                                status="failed",
                                partial=True,
                                completed_pages=len(completed_text),
                                total_pages=total_pages,
                                failed_page=page.number,
                                page_text=completed_text,
                                page_usage=page_usage,
                                uncertain_paid_outcome=uncertain_paid_outcome,
                            )

                        completed_text.append(result["content"])
                        completed_blocks.extend(result["blocks"])

                        stats = _retry_metadata(client)
                        retry_attempts += stats["retry_attempts"]
                        retry_succeeded = (
                            retry_succeeded or stats["retry_succeeded"]
                        )
                        retry_exhausted = (
                            retry_exhausted or stats["retry_exhausted"]
                        )
                        uncertain_paid_outcome = (
                            uncertain_paid_outcome
                            or stats["uncertain_paid_outcome"]
                        )

                        usage = result.get("usage")
                        page_usage.append(
                            usage if isinstance(usage, dict) else {}
                        )

                        if isinstance(usage, dict):
                            for key, value in usage.items():
                                if isinstance(value, bool):
                                    continue

                                if isinstance(value, (int, float)):
                                    current = available_usage.get(key)

                                    if (
                                        isinstance(current, (int, float))
                                        and not isinstance(current, bool)
                                    ):
                                        available_usage[key] = current + value
                                    elif key not in available_usage:
                                        available_usage[key] = value

                return OCROutput(
                    text="\n\n".join(completed_text),
                    blocks=completed_blocks,
                    usage=available_usage,
                    status="success",
                    partial=False,
                    completed_pages=total_pages,
                    total_pages=total_pages,
                    page_text=completed_text,
                    page_usage=page_usage,
                    retry_attempts=retry_attempts,
                    retry_succeeded=retry_succeeded,
                    retry_exhausted=retry_exhausted,
                    uncertain_paid_outcome=uncertain_paid_outcome,
                )

            result = client.ocr(
                path,
                table_format=table_format,
                max_tokens=max_tokens,
            )

            return OCROutput(
                text=result["content"],
                blocks=result["blocks"],
                usage=(
                    result.get("usage")
                    if isinstance(result.get("usage"), dict)
                    else {}
                ),
                status="success",
                completed_pages=1,
                total_pages=1,
                page_text=[result["content"]],
                page_usage=[
                    (
                        result.get("usage")
                        if isinstance(result.get("usage"), dict)
                        else {}
                    )
                ],
                **_retry_metadata(client),
            )

        except (
            ToolError,
            BodhanDeadlineExceeded,
            BodhanAPIError,
            BodhanNetworkError,
            BodhanResponseError,
            ConfigurationError,
            OSError,
            ValueError,
            ValidationError,
        ) as exc:
            if isinstance(exc, ToolError):
                raise

            raise _tool_error("OCR", exc) from None

    return server


def main() -> None:
    """Run the standalone server over stdio."""
    server.run(transport="stdio")


server = create_server()
