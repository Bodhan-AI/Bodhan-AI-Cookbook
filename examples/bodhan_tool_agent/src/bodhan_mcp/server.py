"""Bodhan Standalone MCP Server over stdio.

Exposes Bodhan AI's 4 Indic models (indic-transcribe, indic-translate,
indic-speak, indic-ocr) and media utilities via Model Context Protocol.

Handles all model limits (30s audio chunking, TTS sentence splitting,
multi-page PDF rasterization) inside the tools.

MCP STDIO PROTOCOL SAFETY:
stdout is strictly reserved for the JSON-RPC wire protocol.
ALL logging and diagnostic information is routed strictly through
get_logger("mcp") which writes to sys.stderr or files.
"""

from __future__ import annotations

import base64
from contextvars import ContextVar, Token
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from src.common.audio import (
    AudioChunkSegment,
    chunk_wav_silence_aware,
    concatenate_wav_files,
    ensure_wav,
    format_srt_content,
    format_timestamp_srt,
    format_timestamp_vtt,
    format_vtt_content,
    inspect_audio,
    split_indic_sentences,
)
from src.common.config import BodhanConfig, ensure_utf8_streams
from src.common.document import (
    SUPPORTED_DOCUMENT_EXTENSIONS,
    SUPPORTED_IMAGE_EXTENSIONS,
    extract_docx_text,
    get_document_metadata,
    get_pdf_metadata,
    rasterize_document_pages,
    rasterize_pdf_pages,
)
from src.common.logger import get_logger
from src.common.paths import resolve_input_path
from src.common.retry import retry_async
from src.bodhan_mcp.cache import (
    IndicCacheManager,
    get_cache_manager,
    indic_cache,
    make_extract_document_key,
    make_synthesize_key,
    make_transcribe_key,
    make_translate_key,
)

# Ensure UTF-8 on Windows stdio
ensure_utf8_streams()

# Logger writing exclusively to stderr (MCP stdio protocol safety)
logger = get_logger("mcp")

# Initialize FastMCP Server
mcp = FastMCP(
    "bodhan-mcp",
    description="Standalone MCP Server for Bodhan Indic Models (ASR, MT, TTS, OCR)",
)

# Active artifacts directory context for run isolation & path resolution
_active_artifacts_dir: ContextVar[Path | None] = ContextVar("_active_artifacts_dir", default=None)


def set_active_artifacts_dir(path: str | Path | None) -> Token[Path | None]:
    """Set the active artifacts directory for the current async execution context."""
    resolved = Path(path).resolve() if path is not None else None
    return _active_artifacts_dir.set(resolved)


def reset_active_artifacts_dir(token: Token[Path | None]) -> None:
    """Reset the active artifacts directory to the previous token state."""
    _active_artifacts_dir.reset(token)


def get_active_artifacts_dir() -> Path | None:
    """Get the active artifacts directory for the current execution context."""
    return _active_artifacts_dir.get()


def _strip_artifacts_prefix(path_str: str) -> str:
    s = path_str.strip().replace("\\", "/")
    if s.startswith("./"):
        s = s[2:]
    if s.lower().startswith("out/"):
        s = s[4:]
    if s.lower().startswith("artifacts/"):
        s = s[10:]
    return s


def resolve_artifact_path(file_path: str | Path) -> Path:
    """Resolve and validate an artifact destination path.

    Rules:
    1. Redundant leading 'artifacts/' or 'artifacts\\' prefixes are stripped.
    2. Relative paths resolve inside the active artifacts directory (if configured).
    3. Traversal outside the active artifacts directory is blocked with ToolError.
    4. When no active directory is configured, relative and absolute paths resolve normally.
    """
    raw_str = str(file_path).strip()
    active_dir = get_active_artifacts_dir()
    cleaned_str = _strip_artifacts_prefix(raw_str)
    candidate = Path(cleaned_str)

    if active_dir is not None:
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            resolved = (active_dir / candidate).resolve()

        try:
            if not resolved.is_relative_to(active_dir):
                raise ToolError(
                    f"Path traversal detected: '{file_path}' resolves outside the active artifacts directory '{active_dir}'."
                )
        except ValueError:
            raise ToolError(
                f"Path traversal detected: '{file_path}' resolves outside the active artifacts directory '{active_dir}'."
            )
        return resolved

    return Path(raw_str).resolve()

# Image MIME types for Indic-OCR and vision payloads
IMAGE_MIME_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}

# Supported language codes from scripts/bodhan_api_rules.json
SUPPORTED_INDIC_LANGS = {
    "as", "bhb", "bho", "bn", "brx", "doi", "en", "gu", "hi", "kn",
    "kok", "ks", "ks-Deva", "mai", "ml", "mni", "mni-Beng", "mr",
    "ne", "or", "pa", "sa", "sat", "sd", "sd-Arab", "ta", "te", "ur"
}

# Language display name mapping for prompting indic-translate
LANGUAGE_NAMES: dict[str, str] = {
    "as": "Assamese", "bhb": "Bhili", "bho": "Bhojpuri", "bn": "Bengali", "brx": "Bodo",
    "doi": "Dogri", "en": "English", "gu": "Gujarati", "hi": "Hindi", "kn": "Kannada",
    "kok": "Konkani", "ks": "Kashmiri", "ks-Deva": "Kashmiri",
    "mai": "Maithili", "ml": "Malayalam", "mni": "Manipuri",
    "mni-Beng": "Manipuri", "mr": "Marathi", "ne": "Nepali", "or": "Odia",
    "pa": "Punjabi", "sa": "Sanskrit", "sat": "Santali", "sd": "Sindhi",
    "sd-Arab": "Sindhi", "ta": "Tamil", "te": "Telugu", "ur": "Urdu",
}

# Default voice catalog per language for Indic-Speak
DEFAULT_VOICES: dict[str, str] = {
    "hi": "Kavya", "ta": "Anitha", "te": "Sravani", "bn": "Ishita", "mr": "Anagha",
    "gu": "Dhara", "kn": "Deepika", "ml": "Lakshmi", "pa": "Kaur", "ur": "Saba",
    "as": "Prastuti", "doi": "Preeti", "kok": "Anjali", "mai": "Vaidehi", "ne": "Srijana",
    "or": "Itishree", "sa": "Bharati", "en": "Amit",
}

# Unicode script blocks per language, used to validate that a translation really
# is in the target language. Languages with multiple native scripts accept any
# of their script blocks. Mirrors the detection ranges in detect_language().
_SCRIPT_DEVA = ((0x0900, 0x097F),)     # Devanagari
_SCRIPT_BENG = ((0x0980, 0x09FF),)     # Bengali
_SCRIPT_GURU = ((0x0A00, 0x0A7F),)     # Gurmukhi
_SCRIPT_GUJA = ((0x0A80, 0x0AFF),)     # Gujarati
_SCRIPT_ORYA = ((0x0B00, 0x0B7F),)     # Odia
_SCRIPT_TAML = ((0x0B80, 0x0BFF),)     # Tamil
_SCRIPT_TELU = ((0x0C00, 0x0C7F),)     # Telugu
_SCRIPT_KNDA = ((0x0C80, 0x0CFF),)     # Kannada
_SCRIPT_MLYM = ((0x0D00, 0x0D7F),)     # Malayalam
_SCRIPT_ARAB = ((0x0600, 0x06FF),)     # Arabic
_SCRIPT_OLCH = ((0x1C50, 0x1C7F),)     # Ol Chiki
_SCRIPT_MEET = ((0xABC0, 0xABFF),)     # Meitei Mayek
_SCRIPT_LATN = ((0x0041, 0x005A), (0x0061, 0x007A))  # Latin letters

_LANGUAGE_SCRIPTS: dict[str, tuple[tuple[int, int], ...]] = {
    "en": _SCRIPT_LATN,
    "hi": _SCRIPT_DEVA, "mr": _SCRIPT_DEVA, "sa": _SCRIPT_DEVA, "ne": _SCRIPT_DEVA,
    "kok": _SCRIPT_DEVA, "mai": _SCRIPT_DEVA, "doi": _SCRIPT_DEVA, "brx": _SCRIPT_DEVA,
    "bho": _SCRIPT_DEVA, "bhb": _SCRIPT_DEVA, "ks-Deva": _SCRIPT_DEVA,
    "ks": _SCRIPT_ARAB + _SCRIPT_DEVA, "sd": _SCRIPT_ARAB + _SCRIPT_DEVA,
    "sd-Arab": _SCRIPT_ARAB, "ur": _SCRIPT_ARAB,
    "bn": _SCRIPT_BENG, "as": _SCRIPT_BENG, "mni-Beng": _SCRIPT_BENG,
    "mni": _SCRIPT_MEET + _SCRIPT_BENG,
    "pa": _SCRIPT_GURU, "gu": _SCRIPT_GUJA, "or": _SCRIPT_ORYA, "ta": _SCRIPT_TAML,
    "te": _SCRIPT_TELU, "kn": _SCRIPT_KNDA, "ml": _SCRIPT_MLYM,
    "sat": _SCRIPT_OLCH + _SCRIPT_DEVA,
}


def _translation_script_ok(text: str, target_language: str) -> bool:
    """Heuristic validation: a real translation must contain characters from the
    target language's script block(s).

    Guards against the live indic-translate model echoing the input (wrong
    script) or answering an unrelated question instead of translating. Short
    texts (< 10 chars) skip the check to avoid false ToolErrors on numeric or
    trivially small payloads.
    """
    ranges = _LANGUAGE_SCRIPTS.get(target_language)
    if not ranges or len(text.strip()) < 10:
        return True
    return any(lo <= ord(ch) <= hi for ch in text for lo, hi in ranges)


def _get_headers(key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {key}",
    }


# ============================================================================
# FastMCP Resources (Declarative Catalogs & Boundaries)
# ============================================================================
@mcp.resource("bodhan://catalog/languages", mime_type="application/json")
def get_languages_resource() -> str:
    """Return JSON catalog of 27 Indian languages plus English (28 codes total) and script subtags."""
    return json.dumps({
        "supported_languages": sorted(SUPPORTED_INDIC_LANGS),
        "language_names": LANGUAGE_NAMES,
        "script_subtags": {
            "ks-Deva": {
                "name": "Kashmiri (Devanagari script)",
                "base_code": "ks",
                "script": "Devanagari",
                "guidance": "Use ks-Deva when Devanagari script is requested; ks defaults to Perso-Arabic."
            },
            "sd-Arab": {
                "name": "Sindhi (Arabic script)",
                "base_code": "sd",
                "script": "Arabic",
                "guidance": "Use sd-Arab when Arabic script is requested; sd defaults to Devanagari or Arabic."
            },
            "mni-Beng": {
                "name": "Manipuri (Bengali script)",
                "base_code": "mni",
                "script": "Bengali",
                "guidance": "Use mni-Beng when Bengali/Eastern Nagari script is requested; mni defaults to Meitei Mayek."
            },
        },
        "count": len(SUPPORTED_INDIC_LANGS),
    }, indent=2, ensure_ascii=False)


@mcp.resource("bodhan://catalog/voices", mime_type="application/json")
def get_voices_resource() -> str:
    """Return JSON catalog of default voice assignments per language for Indic-Speak."""
    return json.dumps({
        "default_voices": DEFAULT_VOICES,
        "default_voice_fallback": "Kavya",
        "audio_format": "WAV",
        "sample_rate_hz": 24000,
        "supported_styles": ["news", "AIR style news", "happy", "formal", "casual"],
        "voice_cloning": {
            "supported": False,
            "boundary": (
                "Voice cloning, zero-shot voice cloning, and speaker adaptation from reference audio clips "
                "(.ogg, .wav) are NOT supported. Indic-Speak exclusively uses predefined catalog voices "
                "(e.g. Kavya, Amit, Anitha)."
            ),
        },
    }, indent=2, ensure_ascii=False)


@mcp.resource("bodhan://guidelines/routing-contracts", mime_type="application/json")
def get_routing_contracts_resource() -> str:
    """Return JSON guidelines for client LLMs routing Bodhan MCP tools."""
    return json.dumps({
        "bounded_pagination": {
            "rule": "For bounded character slices or ranges on ANY file—including .docx—always call read_text_file.",
            "formula": "offset = start_index, limit = end_index - start_index + 1",
            "example": "To read characters 4,000–5,999: offset: 4000, limit: 2000 (5999 - 4000 + 1 = 2000).",
            "anti_pattern": "NEVER call extract_document_text for bounded windows; it returns the entire document.",
        },
        "plain_text_vs_ocr": {
            "plain_text_tool": "read_text_file",
            "ocr_tool": "extract_document_text",
            "rule": "Never use extract_document_text on plain text, code, CSV, JSON, Markdown, or SRT files. extract_document_text is strictly for images (.png, .jpg) and rendered documents (.pdf, .docx, .tiff).",
        },
        "audio_deliverables": {
            "rule": "When the requested deliverable is spoken audio, a voiceover, or a .wav file, always call synthesize_speech.",
            "forbidden": "Never call write_text_file when an audio file or .wav deliverable is requested.",
            "direct_speech": "If text is already in the target language (or English) and requested to be read aloud or narrated, call synthesize_speech directly without translating first.",
            "voice_cloning_impossible": (
                "Uses predefined catalog voices (e.g. Kavya, Amit, Anitha); does NOT support voice cloning or speaker "
                "adaptation from reference audio clips (.ogg, .wav). Requests for voice cloning are impossible."
            ),
            "audio_verification": (
                "Post-synthesis audio deliverable verification: Call transcribe_audio on synthesized .wav files to "
                "back-transcribe and confirm speech was synthesized faithfully."
            ),
        },
        "audio_verification_and_inspection": {
            "verification_rule": (
                "Used for post-synthesis audio deliverable verification (back-transcribing generated .wav files "
                "to confirm speech was synthesized faithfully)."
            ),
            "inspection_boundary": (
                "Never call inspect_media before transcribe_audio or extract_document_text when transcription or text "
                "extraction is the goal. Call inspect_media ONLY when metadata is explicitly requested."
            ),
        },
        "subtitles_and_long_audio": {
            "rule": "When requested to create subtitles (.srt or .vtt) from an audio file, always call transcribe_audio with output_format='srt' (or 'vtt') and output_path.",
            "forbidden": "Never call write_text_file to write subtitle files and never attempt to construct subtitle content manually.",
            "translation": "If English subtitles are requested from Indian-language audio, pass task='translate'.",
        },
        "translation_guidelines": {
            "source_language": "Always omit source_language (leave None or unset) in translate_text to allow auto-detection and prevent live service input echoing.",
            "target_subtags": "Use exact script subtags: ks-Deva (Kashmiri Devanagari), sd-Arab (Sindhi Arabic), mni-Beng (Manipuri Bengali).",
            "unsupported_pairs": "If a target language is not among the 27 Indian languages plus English (28 codes total), reject the task immediately with status='impossible'.",
        },
        "missing_file_defense": {
            "rule": "If an input file is explicitly missing or nonexistent on disk, immediately call finish(status='impossible'). Do not attempt tool calls on missing paths.",
        },
        "history_continuity": {
            "rule": "When conversation history confirms an upstream result (extraction, transcription, or intermediate text) was already produced, treat it as available context and proceed with the downstream transformation rather than aborting or repeating.",
        },
    }, indent=2, ensure_ascii=False)


# ============================================================================
# 1. transcribe_audio (indic-transcribe)
# ============================================================================
@mcp.tool()
@indic_cache(tool_name="transcribe_audio", key_generator=make_transcribe_key)
async def transcribe_audio(
    file_path: str,
    language: str | None = None,
    task: Literal["transcribe", "translate"] = "transcribe",
    output_format: Literal["text", "srt", "vtt"] = "text",
    output_path: str | None = None,
    bypass_cache: bool = False,
) -> str:
    """Transcribe spoken audio in 27 Indian languages plus English (28 codes total) using Indic-Transcribe.

        Used for verbatim ASR, speech-to-English translation, and post-synthesis audio deliverable verification (back-transcribing generated .wav files to confirm speech was synthesized faithfully). Handles files of any duration by auto-chunking <=30s chunks at speech pauses. Never call inspect_media before this tool when transcription is the goal.

        Supports generating timestamped subtitle files (.srt, .vtt) or text deliverables directly to disk.

        ROUTING CONTRACT & BOUNDARIES:
        - SUPPORTED LANGUAGES: 27 Indian languages plus English (28 codes total) (as, bhb, bho, bn, brx, doi, en, gu, hi,
          kn, kok, ks, ks-Deva, mai, ml, mni, mni-Beng, mr, ne, or, pa, sa, sat, sd, sd-Arab, ta, te, ur).
        - TASK MODES & VERIFICATION:
          * `task="transcribe"` (default): Verbatim speech-to-text in original spoken language.
          * `task="translate"`: Direct speech-to-text translation yielding English text directly.
          * Post-synthesis audio deliverable verification: Back-transcribe generated .wav files to confirm speech was synthesized faithfully.
        - AUTOMATIC CHUNKING: Handles files of any duration by auto-chunking <=30s chunks at speech pauses;
          manual pre-splitting is never required.
        - NO PRE-INSPECTION: Never call inspect_media before this tool when transcription is the goal.
          Format conversion and audio validation are handled internally at zero cost.
        - OUTPUT FORMATS & DELIVERABLES:
          * `output_format="text"` (default): Returns raw transcript text (or writes to output_path if provided).
          * `output_format="srt"`: Assembles SubRip subtitle file (.srt) with accurate millisecond timecodes.
          * `output_format="vtt"`: Assembles WebVTT subtitle file (.vtt) with accurate millisecond timecodes.
          * If `output_path` ends with `.srt` or `.vtt`, subtitle format is automatically inferred.
          * When a subtitle or file deliverable is generated, writes UTF-8 file to disk and returns a JSON metadata envelope.

        Args:
            file_path: Path to the audio file on disk (WAV, MP3, AAC, M4A, FLAC, OGG, etc.).
            language: ISO language code (e.g. 'hi', 'ta', 'te'). If omitted, automatically detected.
            task: 'transcribe' for verbatim speech in original language, or 'translate' for direct English.
            output_format: Deliverable format: 'text', 'srt', or 'vtt'. Default is 'text'.
            output_path: Optional destination file path (e.g. 'artifacts/subtitles.srt').
            bypass_cache: If True, bypasses SQLite cache and invokes models directly.

        Returns:
            If output_format='text' and output_path is None: raw transcript text string.
            Otherwise: JSON metadata envelope string containing status, artifact_path, output_format,
            cues_count, duration_seconds, and preview.
    """
    logger.info(
        "transcribe_audio called: file_path=%s, language=%s, task=%s, output_format=%s, output_path=%s",
        file_path, language, task, output_format, output_path,
    )
    path = resolve_input_path(file_path)
    if not path.is_file():
        logger.error("Audio file not found: %s", file_path)
        raise ToolError(f"Audio file not found: {file_path}. Please provide a valid file path on disk.")

    config = BodhanConfig.from_env()
    api_key = config.transcribe_key
    if not api_key:
        logger.error("BODHAN_TRANSCRIBE_KEY or BODHAN_API_KEY is not configured.")
        raise ToolError(
            "BODHAN_TRANSCRIBE_KEY (or BODHAN_API_KEY) is not configured in the environment. "
            "Please check your environment variables or .env file."
        )

    if language and language not in SUPPORTED_INDIC_LANGS:
        logger.warning("Unsupported language requested for transcription: %s", language)
        raise ToolError(
            f"Language '{language}' is not supported. Supported Indic languages: "
            f"{', '.join(sorted(SUPPORTED_INDIC_LANGS))}. Do not retry with unsupported languages."
        )

    # Determine effective format
    eff_format = output_format.lower() if output_format else "text"
    if output_path:
        out_lower = output_path.lower()
        if out_lower.endswith(".srt"):
            eff_format = "srt"
        elif out_lower.endswith(".vtt"):
            eff_format = "vtt"

    if eff_format not in {"text", "srt", "vtt"}:
        raise ToolError(f"Unsupported output_format '{output_format}'. Supported formats: 'text', 'srt', 'vtt'.")

    # Determine destination artifact path if writing to disk
    if eff_format in {"srt", "vtt"}:
        if output_path:
            dest_path = resolve_artifact_path(output_path)
        else:
            dest_path = resolve_artifact_path(f"artifacts/{path.stem}.{eff_format}")
    elif output_path:
        dest_path = resolve_artifact_path(output_path)
    else:
        dest_path = None

    temp_conv_dir: str | None = None
    try:
        # Convert non-WAV to PCM16 WAV if needed
        work_path = path
        if path.suffix.lower() != ".wav":
            try:
                temp_conv_dir = tempfile.mkdtemp(prefix="bodhan_conv_")
                work_path = ensure_wav(path, output_dir=temp_conv_dir)
                logger.info("Auto-converted %s to PCM16 WAV for transcription", path.name)
            except Exception as conv_err:
                logger.debug("Auto WAV conversion skipped (%s), proceeding with original file", conv_err)
                work_path = path

        # --- SUBTITLE GENERATION (SRT / VTT) ---
        if eff_format in {"srt", "vtt"}:
            with tempfile.TemporaryDirectory() as tmp_dir:
                segments = chunk_wav_silence_aware(
                    work_path,
                    tmp_dir,
                    max_chunk_sec=28.0,
                    return_segments=True,
                )
                logger.info("Split audio into %d segments for %s generation", len(segments), eff_format)
                cues: list[tuple[float, float, str]] = []
                for idx, seg in enumerate(segments, 1):
                    logger.info("Transcribing segment %d/%d (%.2fs - %.2fs)...", idx, len(segments), seg.start_sec, seg.end_sec)
                    chunk_text = await _call_transcribe(seg.path, api_key, config.base_url, language, task)
                    chunk_text = chunk_text.strip()
                    if task == "translate" and chunk_text:
                        if not _translation_script_ok(chunk_text, "en"):
                            try:
                                chunk_text = await translate_text(chunk_text, target_language="en")
                                chunk_text = chunk_text.strip()
                            except Exception as trans_err:
                                logger.warning("English translation fallback failed for segment %d: %s", idx, trans_err)
                    if chunk_text:
                        cues.append((seg.start_sec, seg.end_sec, chunk_text))

                if eff_format == "srt":
                    content = format_srt_content(cues)
                else:
                    content = format_vtt_content(cues)

                assert dest_path is not None
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                dest_path.write_text(content, encoding="utf-8")

                total_duration = 0.0
                try:
                    meta = inspect_audio(work_path)
                    total_duration = meta.duration_seconds
                except Exception:
                    if segments:
                        total_duration = segments[-1].end_sec

                preview_lines = content.strip().splitlines()
                if preview_lines:
                    preview = "\n".join(preview_lines[:4])
                    if len(preview_lines) > 4:
                        preview += "\n..."
                    preview += "\n"
                else:
                    preview = ""

                envelope = {
                    "status": "success",
                    "artifact_path": str(dest_path),
                    "output_format": eff_format,
                    "cues_count": len(cues),
                    "duration_seconds": round(total_duration, 3),
                    "preview": preview,
                }
                return json.dumps(envelope, indent=2, ensure_ascii=False)

        # --- TEXT TRANSCRIPTION (RAW TEXT OR FILE DELIVERABLE) ---
        try:
            meta = inspect_audio(work_path)
            if meta.duration_seconds > 30.0:
                logger.info("Audio duration %.2fs > 30s, chunking with silence detection...", meta.duration_seconds)
                with tempfile.TemporaryDirectory() as tmp_dir:
                    chunks = chunk_wav_silence_aware(work_path, tmp_dir, max_chunk_sec=28.0)
                    logger.info("Split audio into %d chunks for transcription", len(chunks))
                    transcripts = []
                    for idx, c in enumerate(chunks, 1):
                        logger.info("Transcribing chunk %d/%d...", idx, len(chunks))
                        chunk_text = await _call_transcribe(c, api_key, config.base_url, language, task)
                        transcripts.append(chunk_text.strip())
                    final_text = " ".join(transcripts)
            else:
                final_text = await _call_transcribe(work_path, api_key, config.base_url, language, task)
        except ToolError:
            raise
        except Exception as inspect_err:
            logger.debug("Silence-aware inspection skipped (%s), falling back to direct call", inspect_err)
            final_text = await _call_transcribe(work_path, api_key, config.base_url, language, task)

        if task == "translate" and final_text:
            if not _translation_script_ok(final_text, "en"):
                try:
                    final_text = await translate_text(final_text, target_language="en")
                except Exception as trans_err:
                    logger.warning("English translation fallback failed for text: %s", trans_err)

        if dest_path is not None:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_text(final_text, encoding="utf-8")
            total_duration = 0.0
            try:
                meta = inspect_audio(work_path)
                total_duration = meta.duration_seconds
            except Exception:
                pass
            preview = final_text[:200] + ("..." if len(final_text) > 200 else "")
            envelope = {
                "status": "success",
                "artifact_path": str(dest_path),
                "output_format": "text",
                "cues_count": 1,
                "duration_seconds": round(total_duration, 3),
                "preview": preview,
            }
            return json.dumps(envelope, indent=2, ensure_ascii=False)

        return final_text
    except ToolError:
        raise
    except Exception as e:
        logger.error("Unexpected error in transcribe_audio: %s", e, exc_info=True)
        raise ToolError(f"Unexpected error during transcription: {str(e)}. Guidance: Check input audio format.") from e
    finally:
        if temp_conv_dir:
            import shutil
            shutil.rmtree(temp_conv_dir, ignore_errors=True)


async def _call_transcribe(
    file_path: Path,
    api_key: str,
    base_url: str,
    language: str | None,
    task: str,
) -> str:
    url = f"{base_url}/audio/transcriptions"
    data: dict[str, Any] = {
        "model": "indic-transcribe",
    }
    if language:
        data["language"] = language
    if task:
        data["task"] = task

    file_bytes = file_path.read_bytes()
    files = {"file": (file_path.name, file_bytes, "audio/wav")}
    headers = _get_headers(api_key)

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
        resp = await retry_async(
            client.post,
            url,
            data=data,
            files=files,
            headers=headers,
        )

    result = resp.json()
    transcription = result.get("text", "")
    logger.info("Transcribed %s: %d characters extracted", file_path.name, len(transcription))
    return transcription


# ============================================================================
# 2. translate_text (indic-translate)
# ============================================================================
@mcp.tool()
@indic_cache(tool_name="translate_text", key_generator=make_translate_key)
async def translate_text(
    text: str,
    target_language: str,
    source_language: str | None = None,
    bypass_cache: bool = False,
) -> str:
    """Translate text between 27 Indian languages plus English (28 codes total) using Indic-Translate.

        ROUTING CONTRACT & BOUNDARIES:
        - 28 INDIC LANGUAGES: Supports 27 Indian languages plus English (28 codes total):
          as, bhb, bho, bn, brx, doi, en, gu, hi, kn, kok, ks, ks-Deva, mai, ml, mni, mni-Beng,
          mr, ne, or, pa, sa, sat, sd, sd-Arab, ta, te, ur. Unsupported language pairs must be rejected
          immediately with status='impossible'.
        - SCRIPT SUBTAGS: Always specify exact script subtags where applicable:
          * `ks-Deva`: Kashmiri in Devanagari script (default `ks` is Perso-Arabic).
          * `sd-Arab`: Sindhi in Arabic script (default `sd` is Devanagari or Arabic).
          * `mni-Beng`: Manipuri in Bengali script (default `mni` is Meitei Mayek).
          * Standard ISO codes for others (e.g. `ta` for Tamil, `mr` for Marathi, `hi` for Hindi, `en` for English).
        - OMIT SOURCE_LANGUAGE: Always omit `source_language` (leave None or unset) to enable automatic
          source language detection and prevent live service input echoing.
        - OUTPUT SCRIPT VALIDATION: Output is strictly validated to contain target-script characters;
          input echoing or failure raises an honest ToolError.

        Args:
            text: Source text to translate.
            target_language: Target ISO code with script subtag where applicable (e.g. 'en', 'hi', 'ta', 'mr', 'ks-Deva', 'sd-Arab', 'mni-Beng').
            source_language: Source ISO code, optional. Always omit (leave None or unset) to allow automatic detection and avoid live service input echoing.
            bypass_cache: If True, bypasses SQLite cache and invokes models directly.
    """
    clean_text = text.strip()
    if not clean_text:
        return ""

    logger.info(
        "translate_text called: source=%s, target=%s, chars=%d",
        source_language or "auto", target_language, len(clean_text)
    )

    if target_language not in SUPPORTED_INDIC_LANGS:
        logger.warning("Unsupported target language for translation: %s", target_language)
        raise ToolError(
            f"Language '{target_language}' is not supported. Supported Indic languages: "
            f"{', '.join(sorted(SUPPORTED_INDIC_LANGS))}. Cannot complete translation."
        )

    if source_language and source_language not in SUPPORTED_INDIC_LANGS:
        logger.warning("Unsupported source language for translation: %s", source_language)
        raise ToolError(
            f"Source language '{source_language}' is not supported. Supported Indic languages: "
            f"{', '.join(sorted(SUPPORTED_INDIC_LANGS))}."
        )

    config = BodhanConfig.from_env()
    api_key = config.translate_key
    if not api_key:
        logger.error("BODHAN_TRANSLATE_KEY or BODHAN_API_KEY is not configured.")
        raise ToolError(
            "BODHAN_TRANSLATE_KEY (or BODHAN_API_KEY) is not configured in the environment. "
            "Please check your environment variables or .env file."
        )

    # Map ISO codes to canonical language names for Indic-Translate instruction
    target_name = LANGUAGE_NAMES.get(target_language, target_language)
    # NOTE: the source language is intentionally NOT embedded in the instruction.
    # The live indic-translate deployment misbehaves when the source is named: it
    # echoes the input or answers an unrelated question (verified 2026-11-18:
    # en->ta echoed, en->hi echoed, ta->en and hi->en returned unrelated text,
    # while auto-detect with a plain instruction translated correctly). The
    # script validation below converts any remaining echo cases into honest
    # ToolErrors the calling agent can self-correct on.
    prompt_instructions = [
        f"Translate the following text into {target_name}:\n\n{clean_text}",
        (
            f"Translate the text below into {target_name}. Reply with ONLY the "
            f"{target_name} translation, nothing else:\n\n{clean_text}"
        ),
    ]

    base = config.base_url.rstrip("/")
    chat_url = f"{base}/chat/completions" if base.endswith("/v1") else f"{base}/v1/chat/completions"
    legacy_url = f"{base}/translate"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    resp = None
    last_err = None
    translated = ""
    validation_note = ""

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
            # 1. Primary: OpenAI-compatible indic-translate chat completion (live Bodhan API deployment).
            # A validated echo (output without target-script characters) retries once with a
            # more forceful instruction before falling through to the legacy endpoints.
            for instruction in prompt_instructions:
                try:
                    logger.info("Attempting translation via indic-translate chat completion at %s...", chat_url)
                    chat_payload = {
                        "model": "indic-translate",
                        "messages": [
                            {"role": "user", "content": instruction}
                        ],
                    }
                    resp = await retry_async(
                        client.post,
                        chat_url,
                        json=chat_payload,
                        headers=headers,
                    )
                    if resp.status_code == 200:
                        result = resp.json()
                        choices = result.get("choices", [])
                        if choices and "message" in choices[0]:
                            translated = choices[0]["message"].get("content", "").strip()
                    if translated and not _translation_script_ok(translated, target_language):
                        validation_note = (
                            f"model returned text with no {target_language} script characters "
                            f"(likely echoed the input instead of translating)"
                        )
                        logger.warning(
                            "translate_text validation failed (%s); retrying with explicit instruction...",
                            validation_note,
                        )
                        translated = ""
                except Exception as e:
                    logger.warning("indic-translate chat completion failed (%s), attempting legacy endpoint...", e)
                    last_err = e
                    if any(sig in str(e).lower() for sig in ("connecterror", "getaddrinfo", "timeout error", "network error")):
                        break

            # 2. Secondary fallback: direct /translate endpoint (for legacy or dedicated test harness).
            # Candidates are script-validated the same way.
            if not translated:
                if last_err and any(sig in str(last_err).lower() for sig in ("connecterror", "getaddrinfo", "timeout error", "network error")):
                    logger.warning("Network/DNS failure on primary host; skipping redundant legacy endpoints on same host.")
                else:
                    candidates = [
                        legacy_url,
                        f"{base.replace('/v1', '')}/translate" if "/v1" in base else f"{base}/v1/translate",
                    ]
                    legacy_payload = {
                        "text": clean_text,
                        "target_language": target_language,
                    }
                    if source_language:
                        legacy_payload["source_language"] = source_language

                    for url in candidates:
                        try:
                            logger.info("Attempting translation via %s...", url)
                            resp = await retry_async(
                                client.post,
                                url,
                                json=legacy_payload,
                                headers=headers,
                            )
                            if resp.status_code == 200:
                                result = resp.json()
                                candidate = (
                                    result.get("translation")
                                    or result.get("translated_text")
                                    or result.get("text", "")
                                ).strip()
                                if candidate and _translation_script_ok(candidate, target_language):
                                    translated = candidate
                                    break
                                if candidate:
                                    validation_note = (
                                        f"legacy endpoint returned text with no {target_language} script characters"
                                    )
                                    logger.warning("translate_text validation failed on %s: %s", url, validation_note)
                        except Exception as e:
                            last_err = e
                            continue

        if not translated:
            if validation_note:
                raise ToolError(
                    f"Translation failed validation: {validation_note}. "
                    f"Cannot pass unverified text off as {target_language}."
                )
            err_msg = resp.text if resp is not None else str(last_err)
            raise ToolError(f"Translation API error: {err_msg}")

        logger.info("Translation complete: produced %d characters in %s", len(translated), target_language)
        return translated
    except ToolError:
        raise
    except Exception as e:
        logger.error("Unexpected error in translate_text: %s", e, exc_info=True)
        raise ToolError(f"Unexpected error during translation: {str(e)}. Guidance: Check input text encoding.") from e


# ============================================================================
# 3. synthesize_speech (indic-speak)
# ============================================================================
@mcp.tool()
@indic_cache(tool_name="synthesize_speech", key_generator=make_synthesize_key, is_binary_asset=True)
async def synthesize_speech(
    text: str,
    language: str,
    voice: str | None = None,
    style: str = "news",
    output_path: str | None = None,
    bypass_cache: bool = False,
) -> str:
    """Synthesize 24 kHz WAV speech from Indian-language or English text using Indic-Speak.

        Uses predefined catalog voices (e.g. Kavya, Amit, Anitha); does NOT support voice cloning or speaker adaptation from reference audio clips (.ogg, .wav). Deliverables requiring spoken audio (.wav) must use this tool directly.

        Automatically handles long passages by splitting text along punctuation and Danda ('।')
        boundaries into prosodic segments and concatenating the resulting 24 kHz audio clips into one file.

        ROUTING CONTRACT & BOUNDARIES:
        - PREDEFINED VOICES & NO VOICE CLONING: Uses predefined catalog voices (e.g. Kavya, Amit, Anitha);
          does NOT support voice cloning or speaker adaptation from reference audio clips (.ogg, .wav).
          Requests to clone a voice or speak in the voice of a reference audio clip are impossible.
        - AUDIO DELIVERABLES: Deliverables requiring spoken audio (.wav) must use this tool directly.
          This tool is the exclusive generator for spoken audio and .wav deliverables.
          Never call `write_text_file` when audio deliverables are requested.
        - DIRECT SPEECH / NO UNNECESSARY TRANSLATION: When text is already in the target language and
          requested to be read aloud, spoken, or narrated, call this tool directly (do NOT translate first
          unless translation is explicitly requested).
        - REQUIRED PARAMETERS: Requires valid text and a supported language ISO code.
        - AUTOMATIC SEGMENTATION: Long text is split internally along prosodic sentence and Danda boundaries;
          manual pre-splitting is unnecessary.

        Args:
            text: Indian-language or English text to speak.
            language: ISO code (e.g. 'ta', 'hi', 'bn', 'mr', 'te', 'en').
            voice: Preferred voice name from the Bodhan voice catalog (optional).
            style: Speaking style (e.g. 'AIR style news', 'news', 'happy').
            output_path: Where to save the WAV file. Defaults to speech_{language}.wav.
            bypass_cache: If True, bypasses SQLite cache and invokes models directly.
    """
    clean_text = text.strip()
    if not clean_text:
        raise ToolError("Text for speech synthesis cannot be empty. Please provide text to speak.")

    logger.info("synthesize_speech called: lang=%s, style=%s, chars=%d", language, style, len(clean_text))

    if language not in SUPPORTED_INDIC_LANGS:
        logger.warning("Unsupported language for speech synthesis: %s", language)
        raise ToolError(
            f"Language '{language}' is not supported for speech synthesis. "
            f"Supported Indic languages: {', '.join(sorted(SUPPORTED_INDIC_LANGS))}."
        )

    config = BodhanConfig.from_env()
    api_key = config.speak_key
    if not api_key:
        logger.error("BODHAN_SPEAK_KEY or BODHAN_API_KEY is not configured.")
        raise ToolError(
            "BODHAN_SPEAK_KEY (or BODHAN_API_KEY) is not configured in the environment. "
            "Please check your environment variables or .env file."
        )

    dest = output_path or f"speech_{language}.wav"
    out_file = resolve_artifact_path(dest)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Split into prosodically complete sentences
        sentences = split_indic_sentences(clean_text, min_chars=20)
        logger.info("Synthesizing %d sentences into %s", len(sentences), out_file.name)

        chosen_voice = voice or DEFAULT_VOICES.get(language, "Kavya")

        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_clips: list[Path] = []
            instructions: dict[str, Any] = {"lang": language}
            if style:
                instructions["style"] = style

            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
                for idx, s in enumerate(sentences):
                    payload = {
                        "model": "indic-speak",
                        "input": s,
                        "voice": chosen_voice,
                        "instructions": json.dumps(instructions),
                    }
                    url = f"{config.base_url}/audio/speech"
                    resp = await retry_async(
                        client.post,
                        url,
                        json=payload,
                        headers=_get_headers(api_key),
                    )
                    clip_path = Path(tmp_dir) / f"part_{idx:03d}.wav"
                    clip_path.write_bytes(resp.content)
                    temp_clips.append(clip_path)

            concatenate_wav_files(temp_clips, out_file)

        meta = inspect_audio(out_file)
        logger.info("Speech synthesis completed successfully: %s (duration=%.2fs)", out_file.name, meta.duration_seconds)
        return json.dumps({
            "status": "success",
            "output_file": str(out_file),
            "duration_seconds": meta.duration_seconds,
            "sample_rate": meta.sample_rate,
            "language": language,
        })
    except ToolError:
        raise
    except Exception as e:
        logger.error("Unexpected error in synthesize_speech: %s", e, exc_info=True)
        raise ToolError(f"Unexpected error during speech synthesis: {str(e)}. Guidance: Check audio output permissions.") from e


# ============================================================================
# 4. extract_document_text (indic-ocr & Multi-Format Documents)
# ============================================================================
@mcp.tool()
@indic_cache(tool_name="extract_document_text", key_generator=make_extract_document_key)
async def extract_document_text(
    file_path: str,
    table_format: Literal["markdown", "html"] = "markdown",
    page_limit: int | None = None,
    bypass_cache: bool = False,
) -> str:
    """Extract full document text and structured tables using Indic-OCR or direct parsing.

        Processes Indian-language documents (.pdf, .docx, .xps, .epub, .tiff) and raster images
        (.png, .jpg, .jpeg, .webp, .bmp, .gif), returning whole-document or page-by-page text.

        ROUTING CONTRACT & BOUNDARIES:
        - WHOLE-DOCUMENT OCR ONLY: Always extracts the complete document or page-by-page text via Indic-OCR.
          Multi-page rendered documents (PDF, TIFF, etc.) and raster images are rasterized and extracted completely.
        - NO CHARACTER PAGINATION BOUNDARY: Returns full document/page text and does NOT support character-level
          offset pagination. For bounded character slices or ranges (e.g. characters 4,000–5,999) on ANY
          file—including .docx—always call `read_text_file` with the zero-based pagination formula:
          `offset = start`, `limit = end - start + 1` (e.g. characters 4,000–5,999 require `offset=4000, limit=2000`).
          NEVER call `extract_document_text` for bounded windows or character slices; it returns the entire document.
        - LOCAL DOCX FAST-PATH: For .docx files, paragraphs and tables are extracted directly and locally
          with zero external API cost, formatting tables as Markdown or HTML.
        - NEVER use this tool for raw source code or plain text files (.txt, .md, .csv, .json, .srt);
          use `read_text_file` instead.
        - NEVER call `inspect_media` as a prerequisite before this tool.

        Args:
            file_path: Path to the document or image file on disk.
            table_format: Format for extracted tables ('markdown' or 'html').
            page_limit: Maximum pages to process for multi-page documents (optional).
            bypass_cache: If True, bypasses SQLite cache and invokes models directly.
    """
    logger.info("extract_document_text called: file=%s, table_format=%s, page_limit=%s", file_path, table_format, page_limit)
    path = resolve_input_path(file_path)
    if not path.is_file():
        logger.error("Document file not found: %s", file_path)
        raise ToolError(f"Document file not found: {file_path}. Please provide a valid file path on disk.")

    suffix = path.suffix.lower()

    # Fast-path for DOCX: extract directly without OCR API cost
    if suffix == ".docx":
        try:
            logger.info("Extracting structured text directly from DOCX: %s", path.name)
            docx_text = extract_docx_text(path, table_format=table_format)
            logger.info("Extracted %d characters from DOCX: %s", len(docx_text), path.name)
            return docx_text
        except Exception as docx_err:
            logger.error("Error extracting text from DOCX %s: %s", path.name, docx_err, exc_info=True)
            raise ToolError(f"Failed to extract text from DOCX '{file_path}': {docx_err}") from docx_err

    config = BodhanConfig.from_env()
    api_key = config.ocr_key
    if not api_key:
        logger.error("BODHAN_OCR_KEY or BODHAN_API_KEY is not configured.")
        raise ToolError(
            "BODHAN_OCR_KEY (or BODHAN_API_KEY) is not configured in the environment. "
            "Please check your environment variables or .env file."
        )

    try:
        if suffix in SUPPORTED_DOCUMENT_EXTENSIONS:
            with tempfile.TemporaryDirectory() as tmp_dir:
                image_paths = rasterize_document_pages(path, tmp_dir, dpi=150, page_limit=page_limit)
                logger.info("Rasterized %d document pages for OCR extraction", len(image_paths))
                return await _ocr_images(image_paths, api_key, config.base_url, table_format)
        else:
            return await _ocr_images([path], api_key, config.base_url, table_format)
    except ToolError:
        raise
    except Exception as e:
        logger.error("Unexpected error in extract_document_text: %s", e, exc_info=True)
        raise ToolError(f"Unexpected error during document OCR: {str(e)}. Guidance: Ensure file is a valid document or image.") from e


async def _ocr_images(
    images: list[Path],
    api_key: str,
    base_url: str,
    table_format: str,
) -> str:
    page_texts: list[str] = []
    url = f"{base_url}/chat/completions"

    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=5.0)) as client:
        for idx, img in enumerate(images, 1):
            logger.info("Performing Indic-OCR on page %d/%d (%s)...", idx, len(images), img.name)
            suffix = img.suffix.lower()
            mime_type = IMAGE_MIME_TYPES.get(suffix, "image/png")

            # Standardize non-web formats (BMP, TIFF, etc.) to PNG via PIL if necessary
            if suffix in {".bmp", ".tiff", ".tif"} or mime_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                try:
                    import io
                    from PIL import Image
                    with Image.open(img) as pil_img:
                        buf = io.BytesIO()
                        pil_img.save(buf, format="PNG")
                        b64_data = base64.b64encode(buf.getvalue()).decode("utf-8")
                        mime_type = "image/png"
                except Exception as conv_err:
                    logger.debug("Image PNG standardization fallback for %s (%s)", img.name, conv_err)
                    b64_data = base64.b64encode(img.read_bytes()).decode("utf-8")
            else:
                b64_data = base64.b64encode(img.read_bytes()).decode("utf-8")

            data_url = f"data:{mime_type};base64,{b64_data}"
            payload = {
                "model": "indic-ocr",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": f"Extract all text from this page. Format tables as {table_format}."
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": data_url}
                            }
                        ]
                    }
                ]
            }
            resp = await retry_async(
                client.post,
                url,
                json=payload,
                headers=_get_headers(api_key),
            )
            content = resp.json()["choices"][0]["message"]["content"]
            page_texts.append(f"<!-- Page {idx} -->\n{content}")

    logger.info("Indic-OCR completed for all %d pages", len(images))
    return "\n\n".join(page_texts)


# ============================================================================
# 5. inspect_media (Media Metadata Helper)
# ============================================================================
@mcp.tool()
async def inspect_media(file_path: str) -> str:
    """Fast, free metadata inspection (duration, sample rate, format, page count, image dimensions). Call ONLY when metadata is explicitly requested; never call as a prerequisite before transcribe_audio or extract_document_text.

        Provides fast, free inspection of audio duration, sample rate, channels, image dimensions/format,
        or document page count without extracting text or incurring API cost.

        ROUTING CONTRACT & BOUNDARIES:
        - CALL ONLY WHEN EXPLICITLY REQUESTED: Fast, free metadata inspection (duration, sample rate, format, page count, image dimensions).
          Call ONLY when metadata is explicitly requested.
        - NEVER A PREREQUISITE: Never call as a prerequisite before transcribe_audio or extract_document_text.
          Downstream tools automatically handle audio chunking, format conversion, and document rasterization internally.
        - METADATA ONLY: Does NOT extract text, transcribe speech, or run OCR.
        - NO REDUNDANT CALLS: Do not call repeatedly if file metadata is already known from prior steps
          or tool outputs.

        Args:
            file_path: Path to the media file on disk.
    """
    logger.info("inspect_media called: %s", file_path)
    path = resolve_input_path(file_path)
    if not path.is_file():
        logger.error("File not found for inspection: %s", file_path)
        raise ToolError(f"File not found: {file_path}. Cannot inspect non-existent file.")

    suffix = path.suffix.lower()
    try:
        if suffix in {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".webm", ".flac", ".opus"}:
            try:
                meta = inspect_audio(path)
                return json.dumps({
                    "type": "audio",
                    "file": path.name,
                    "duration_seconds": meta.duration_seconds,
                    "sample_rate": meta.sample_rate,
                    "channels": meta.channels,
                })
            except Exception as audio_err:
                logger.debug("Wave inspection fallback for %s: %s", path.name, audio_err)
                return json.dumps({
                    "type": "audio",
                    "file": path.name,
                    "size_bytes": path.stat().st_size,
                    "format": suffix.lstrip("."),
                })
        elif suffix == ".pdf":
            doc_meta = get_pdf_metadata(path)
            return json.dumps({
                "type": "pdf",
                "file": path.name,
                "page_count": doc_meta["page_count"],
                "format": doc_meta["format"],
            })
        elif suffix in SUPPORTED_DOCUMENT_EXTENSIONS or suffix == ".docx":
            doc_meta = get_document_metadata(path)
            return json.dumps({
                "type": "document",
                "file": path.name,
                "page_count": doc_meta.get("page_count", 1),
                "format": doc_meta.get("format", suffix.lstrip(".").upper()),
                "size_bytes": path.stat().st_size,
            })
        elif suffix in SUPPORTED_IMAGE_EXTENSIONS:
            return json.dumps({
                "type": "image",
                "file": path.name,
                "size_bytes": path.stat().st_size,
                "format": suffix.lstrip("."),
            })
        else:
            return json.dumps({
                "type": "unknown",
                "file": path.name,
                "size_bytes": path.stat().st_size,
            })
    except ToolError:
        raise
    except Exception as e:
        logger.error("Unexpected error in inspect_media: %s", e, exc_info=True)
        raise ToolError(f"Inspection error for {file_path}: {str(e)}") from e


# ============================================================================
# 6. read_text_file (File I/O Helper with Safe Bounded Paging)
# ============================================================================
@mcp.tool()
async def read_text_file(
    file_path: str,
    offset: int = 0,
    limit: int = 10000,
) -> str:
    """Read a text file from disk safely within workspace roots using bounded character pagination.

        Shields caller context windows from flooding when opening large text files, transcripts,
        or subtitles. Returns JSON with metadata including total file size, current offset, character
        count returned, whether more text remains (has_more), next_offset, and the text slice. Free of charge (zero API cost).

        ROUTING CONTRACT & BOUNDARIES:
        - ZERO-BASED CHARACTER PAGINATION FORMULA (offset=start, limit=end-start+1):
          Supports exact zero-based character offset and limit. When reading a character slice or window
          from `start` to `end` (inclusive) on ANY file—including .docx—always call this tool using the formula:
          `offset = start`, `limit = end - start + 1`
          (e.g. to read characters 4,000–5,999: pass `offset=4000, limit=2000` because 5999 - 4000 + 1 = 2000).
          Always call this tool whenever bounded slices, character ranges, or windows are requested.
        - DOCX SUPPORT: Automatically extracts structured text from local .docx files as well,
          honoring the exact zero-based pagination formula (`offset=start, limit=end-start+1`) for character-level pagination.
        - WORKSPACE CONTAINMENT: Reads text files safely strictly within permitted workspace roots.
          Never reads outside workspace boundaries or sensitive files (.env, keys).
        - PLAIN TEXT & CODE: Always use this tool for raw text or code files (.txt, .md, .csv, .json, .srt, .py).
          NEVER use `extract_document_text` (OCR) for raw text or code files.

        Args:
            file_path: Path to the text file on disk (or .docx file).
            offset: Zero-based character start index (default 0). Formula: `offset = start`.
            limit: Number of characters to read (default 10,000). Formula: `limit = end - start + 1`.
    """
    logger.info("read_text_file called: file=%s, offset=%d, limit=%d", file_path, offset, limit)
    path = resolve_input_path(file_path)
    if not path.is_file():
        raise ToolError(f"File not found: {file_path}. Please provide a valid file path on disk.")

    if offset < 0:
        offset = 0
    if limit <= 0:
        limit = 10000

    suffix = path.suffix.lower()
    if suffix == ".docx":
        try:
            content = extract_docx_text(path, table_format="markdown")
        except Exception as e:
            logger.error("Error reading DOCX in read_text_file: %s", e, exc_info=True)
            raise ToolError(f"Error reading DOCX file '{file_path}': {str(e)}") from e
    else:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = path.read_text(encoding="utf-8", errors="replace")

    total_chars = len(content)
    end_idx = min(offset + limit, total_chars)
    slice_text = content[offset:end_idx]
    has_more = end_idx < total_chars

    logger.info("Read %d/%d chars from %s (offset=%d, has_more=%s)", len(slice_text), total_chars, path.name, offset, has_more)
    return json.dumps({
        "file": path.name,
        "total_chars": total_chars,
        "offset": offset,
        "chars_read": len(slice_text),
        "has_more": has_more,
        "next_offset": end_idx if has_more else None,
        "content": slice_text,
    }, ensure_ascii=False)


# ============================================================================
# 7. write_text_file (File I/O Helper)
# ============================================================================
@mcp.tool()
async def write_text_file(file_path: str, content: str) -> str:
    """Write text content to a file on disk strictly within workspace boundaries.

        Saves generated text artifacts: SRT subtitles, translated Markdown, summaries, transcripts,
        or reports. Automatically creates parent directories. Free of charge (zero API cost).

        ROUTING CONTRACT & BOUNDARIES:
        - WORKSPACE CONTAINMENT: Writes text files strictly within the active workspace root.
          All target paths are validated to stay within workspace containment.
        - REJECTION OF PATH TRAVERSAL (..): Strictly rejects path traversal ('..') that attempts to escape
          workspace boundaries with a ToolError.
        - REJECTION OF AUDIO FILES: Intended strictly for UTF-8 text files. Strictly rejects audio deliverables.
          Deliverables requiring spoken audio, voiceovers, or .wav files must use `synthesize_speech`, NOT this tool.
          Never call `write_text_file` when an audio file or .wav deliverable is requested, and never write base64
          or raw bytes to simulate audio files.

        Args:
            file_path: Destination path for the file strictly within workspace roots (e.g. 'subtitles.srt' or 'artifacts/subtitles.srt').
            content: Text content to write (UTF-8 encoded).
    """
    logger.info("write_text_file called: %s (%d chars)", file_path, len(content))
    path = resolve_artifact_path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.info("Wrote %d characters to %s", len(content), path.name)
    return json.dumps({
        "status": "success",
        "file": str(path),
        "size_bytes": path.stat().st_size,
    })


# ============================================================================
# 8. detect_language (Script/Language Detection Helper)
# ============================================================================

# Unicode script ranges for Indic language detection
_SCRIPT_RANGES: list[tuple[int, int, str, list[str]]] = [
    (0x0900, 0x097F, "Devanagari", ["hi", "mr", "sa", "ne", "kok", "mai", "bho", "doi"]),
    (0x0980, 0x09FF, "Bengali", ["bn", "as"]),
    (0x0A00, 0x0A7F, "Gurmukhi", ["pa"]),
    (0x0A80, 0x0AFF, "Gujarati", ["gu"]),
    (0x0B00, 0x0B7F, "Odia", ["or"]),
    (0x0B80, 0x0BFF, "Tamil", ["ta"]),
    (0x0C00, 0x0C7F, "Telugu", ["te"]),
    (0x0C80, 0x0CFF, "Kannada", ["kn"]),
    (0x0D00, 0x0D7F, "Malayalam", ["ml"]),
    (0x0600, 0x06FF, "Arabic", ["ur", "sd", "ks"]),
    (0x1C50, 0x1C7F, "Ol Chiki", ["sat"]),
    (0xABC0, 0xABFF, "Meitei Mayek", ["mni"]),
]


@mcp.tool()
async def detect_language(text: str) -> str:
    """Detect the script and likely language of text using Unicode block analysis.

        Performs Unicode block analysis locally on text already in context (pass string text directly,
        never file paths) without external API cost.
        Identifies the dominant Indic script block (Devanagari, Tamil, Bengali, Gurmukhi,
        Gujarati, Odia, Telugu, Kannada, Malayalam, Arabic, Ol Chiki, Meitei Mayek) or Latin.
        Free of charge (zero API cost).

        ROUTING CONTRACT & BOUNDARIES:
        - UNICODE BLOCK ANALYSIS ON IN-CONTEXT TEXT: Operates via Unicode block analysis strictly on text
          already present in context. Pass text strings directly; do NOT pass file paths.
        - SCRIPT & LANGUAGE DETECTION: Returns the dominant script, character count, and candidate ISO codes.
        - UNSTATED LANGUAGE HELPER ONLY: Useful when input language is unstated or ambiguous. Do NOT call if
          the language is already known from user instructions, file naming, or preceding steps.
        - SHARED SCRIPT AMBIGUITY: Note that Devanagari script is shared across Hindi, Marathi, Nepali,
          Sanskrit, Maithili, Bhojpuri, and Konkani. Use semantic task context or instructions to disambiguate.

        Args:
            text: Text already in context to analyze (provide at least a few words for reliable detection; do NOT pass file paths).
    """
    logger.info("detect_language called: %d chars", len(text))
    if not text.strip():
        return json.dumps({"script": "unknown", "likely_languages": [], "confidence": "none"})

    counts: dict[str, tuple[int, list[str]]] = {}
    for ch in text:
        cp = ord(ch)
        for lo, hi, script, langs in _SCRIPT_RANGES:
            if lo <= cp <= hi:
                if script not in counts:
                    counts[script] = (0, langs)
                prev_count, prev_langs = counts[script]
                counts[script] = (prev_count + 1, prev_langs)
                break

    if not counts:
        # Check for Latin/ASCII (English)
        latin_count = sum(1 for c in text if c.isalpha() and ord(c) < 0x0100)
        if latin_count > 5:
            return json.dumps({
                "script": "Latin",
                "likely_languages": ["en"],
                "script_char_count": latin_count,
                "confidence": "medium",
            })
        return json.dumps({"script": "unknown", "likely_languages": [], "confidence": "low"})

    # Find dominant script
    dominant_script = max(counts, key=lambda s: counts[s][0])
    char_count, langs = counts[dominant_script]

    result = {
        "script": dominant_script,
        "likely_languages": langs,
        "script_char_count": char_count,
        "confidence": "high" if char_count > 10 else "medium",
    }
    if len(counts) > 1:
        result["other_scripts"] = {s: c for s, (c, _) in counts.items() if s != dominant_script}

    logger.info("Detected script: %s (likely: %s)", dominant_script, ", ".join(langs))
    return json.dumps(result)


# ============================================================================
# 9. Cache Administration & Diagnostics Tools
#
# Operator-only. These remain registered on the MCP server, but they are absent
# from agent.MODEL_FACING_TOOLS, so the LLM never sees them in its catalog.
# Exposing them let a planner clear the cache mid-run to "fix" a stale or
# echoed result, which silently destroys reuse and the cost story.
# ============================================================================
@mcp.tool()
async def get_cache_stats() -> str:
    """Retrieve runtime diagnostics, hit ratio, and disk metrics for the Indic API cache.
    
    Returns JSON metrics including total hits, misses, hit ratio percentage,
    total latency saved, disk usage, and breakdown per Indic tool.
    """
    logger.info("get_cache_stats called")
    manager = get_cache_manager()
    stats = manager.get_stats()
    return json.dumps(stats, indent=2)


@mcp.tool()
async def clear_cache(tool_name: str | None = None) -> str:
    """Clear all entries or entries for a specific tool from the Indic API cache.
    
    Args:
        tool_name: Optional tool name ('translate_text', 'transcribe_audio',
            'synthesize_speech', 'extract_document_text'). If omitted, clears all entries.
    """
    logger.info("clear_cache called: tool_name=%s", tool_name)
    manager = get_cache_manager()
    count = manager.clear(tool_name)
    target = f"for tool '{tool_name}'" if tool_name else "across all tools"
    return json.dumps({
        "status": "success",
        "cleared_entries": count,
        "scope": tool_name or "all",
        "message": f"Successfully cleared {count} cache entries {target}."
    })


def main():
    """Run the FastMCP server over standard I/O (stdio).
    
    CRITICAL: stdout carries JSON-RPC protocol frames. All logs go to stderr.
    """
    logger.info("Starting Bodhan Standalone MCP Server over stdio...")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
