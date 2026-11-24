"""MCP server exposing Bodhan model operations as typed tools."""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ValidationError

from .audio_plan import detect_silences, extract_window, plan_windows, probe_duration
from .checks import check_translation, is_placeholder_text
from .client import BodhanClient
from .cost import ocr_cost, stt_cost, translate_cost, tts_cost
from .http_client import HttpBodhanClient
from .languages import (
    STYLES,
    all_voice_names,
    check_support,
    normalize_language,
    pick_voice,
)
from .subtitles import segments_to_srt
from .textutil import TRANSLATE_CHUNK_CHARS, TTS_CHUNK_CHARS, chunk_text, translation_token_caps


class ToolErrorDetail(BaseModel):
    code: str
    message: str


class ToolErrorOutput(BaseModel):
    ok: Literal[False]
    error: ToolErrorDetail


class TranscribeOutput(BaseModel):
    ok: Literal[True]
    text: str
    windows: int
    duration_seconds: float
    cost_inr: float


class TranslationVerification(BaseModel):
    ok: bool
    issues: list[str]


class TranslateOutput(BaseModel):
    ok: Literal[True]
    text: str
    truncated: bool
    verification: TranslationVerification
    chunks: int
    cost_inr: float


class SynthesizeOutput(BaseModel):
    ok: Literal[True]
    artifact_path: str
    format: Literal["wav", "mp3"]
    chunks: int
    characters: int
    cost_inr: float
    disclaimer: str


class OcrOutput(BaseModel):
    ok: Literal[True]
    markdown: str
    blocks: list[dict[str, Any]]
    pages: int
    cost_inr: float


class InspectMediaOutput(BaseModel):
    ok: Literal[True]
    kind: str
    path: str
    suffix: str | None = None
    metadata: dict[str, Any] | None = None


class ListVoicesOutput(BaseModel):
    ok: Literal[True]
    voice: str | None = None
    reason: str | None = None
    voices: list[str] | None = None
    styles: list[str] | None = None


class LanguageSupportOutput(BaseModel):
    ok: Literal[True]
    supported: bool
    language: str | None = None
    name: str | None = None
    reason: str
    supported_languages: list[str] | None = None


class SubtitlesOutput(BaseModel):
    ok: Literal[True]
    srt: str


class SubtitleSegmentInput(BaseModel):
    start: float
    end: float
    text: str


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def _combine_audio(parts: list[Any]) -> tuple[bytes, str]:
    if not parts:
        raise ValueError("Speech synthesis returned no audio chunks.")
    formats = {part.format.lower() for part in parts}
    if len(formats) != 1:
        raise ValueError("Speech chunks have inconsistent audio formats.")
    audio_format = formats.pop()
    if audio_format == "wav":
        params = None
        frames: list[bytes] = []
        for part in parts:
            with wave.open(io.BytesIO(part.audio_bytes), "rb") as audio:
                current = audio.getparams()
                if params is None:
                    params = current
                elif current[:3] != params[:3] or current[4:] != params[4:]:
                    raise ValueError("Speech chunks have incompatible WAV parameters.")
                frames.append(audio.readframes(audio.getnframes()))
        output = io.BytesIO()
        with wave.open(output, "wb") as audio:
            audio.setparams(params)
            audio.writeframes(b"".join(frames))
        return output.getvalue(), audio_format

    if audio_format == "mp3":
        with tempfile.TemporaryDirectory(prefix="bodhan-audio-") as temp_dir:
            folder = Path(temp_dir)
            for index, part in enumerate(parts):
                (folder / f"part-{index:04d}.mp3").write_bytes(part.audio_bytes)
            manifest = folder / "concat.txt"
            manifest.write_text(
                "".join(f"file '{folder / f'part-{index:04d}.mp3'}'\n" for index in range(len(parts))),
                encoding="utf-8",
            )
            target = folder / "combined.mp3"
            result = subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat",
                 "-safe", "0", "-i", str(manifest), "-c", "copy", str(target)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Could not combine MP3 speech chunks: {result.stderr.strip()[:500]}")
            return target.read_bytes(), audio_format
    raise ValueError(f"Unsupported speech audio format: {audio_format}")


def inspect_media(path: str) -> dict[str, Any]:
    """Return ffprobe metadata for an audio, video, PDF, or image path."""
    source = Path(path)
    if not source.is_file():
        return _error("FILE_NOT_FOUND", f"Media file does not exist: {source}")
    if source.suffix.lower() == ".pdf":
        return {"ok": True, "kind": "pdf", "path": str(source), "suffix": ".pdf"}

    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
             "-of", "json", str(source)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return _error("FFPROBE_NOT_FOUND", "ffprobe was not found on PATH. Install ffmpeg.")
    except subprocess.TimeoutExpired:
        return _error("MEDIA_INSPECTION_TIMEOUT", "ffprobe timed out while inspecting the media file.")
    if result.returncode != 0:
        return _error("MEDIA_INSPECTION_FAILED", result.stderr.strip()[:500] or "ffprobe could not inspect the file")

    try:
        return {"ok": True, "kind": "media", "path": str(source), "metadata": json.loads(result.stdout)}
    except json.JSONDecodeError:
        return _error("MEDIA_INSPECTION_FAILED", "ffprobe returned invalid JSON")


def list_voices(language: str | None = None, gender: str | None = None) -> dict[str, Any]:
    try:
        if language is None and gender is None:
            return {"ok": True, "voices": all_voice_names(), "styles": STYLES}
        if language is not None:
            support = check_support("speak", language)
            if not support["supported"]:
                return _error("LANGUAGE_UNSUPPORTED", support["reason"])
        return {"ok": True, **pick_voice(language, gender)}
    except ValueError as exc:
        return _error("INVALID_VOICE", str(exc))


def make_subtitles(segments: list[dict[str, Any]], max_chars_per_line: int = 42) -> dict[str, Any]:
    if max_chars_per_line < 10:
        return _error("INVALID_SUBTITLE_FORMAT", "max_chars_per_line must be at least 10")
    try:
        return {"ok": True, "srt": segments_to_srt(segments, max_chars_per_line)}
    except (KeyError, TypeError, ValueError) as exc:
        return _error("INVALID_SUBTITLE_SEGMENTS", str(exc))


def _read_timed_segments(source_path: str) -> list[dict[str, Any]]:
    source = Path(source_path)
    if source.suffix.lower() != ".json":
        raise ValueError("UNSUPPORTED_TIMESTAMP_SOURCE: provide a JSON file with explicit start, end, and text fields.")
    if not source.is_file():
        raise ValueError(f"FILE_NOT_FOUND: timed-segment file does not exist: {source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"INVALID_TIMED_SEGMENTS: could not read JSON timed segments: {exc}") from exc
    segments = payload.get("segments") if isinstance(payload, dict) else payload
    if not isinstance(segments, list):
        raise ValueError("INVALID_TIMED_SEGMENTS: expected a JSON array or an object with a 'segments' array.")
    validated = []
    for index, value in enumerate(segments):
        try:
            segment = SubtitleSegmentInput.model_validate(value)
        except ValidationError as exc:
            raise ValueError(f"INVALID_TIMED_SEGMENTS: segment {index + 1} needs start, end, and text: {exc}") from exc
        if segment.start < 0 or segment.end <= segment.start:
            raise ValueError(f"INVALID_TIMED_SEGMENTS: segment {index + 1} has invalid time bounds.")
        validated.append(segment.model_dump())
    return validated


def create_server(client: BodhanClient | None = None) -> MCPServer:
    bodhan = client if client is not None else HttpBodhanClient.from_env()
    server = MCPServer(name="bodhan-indic-tools", version="0.1.0")

    @server.tool(
        name="bodhan_transcribe",
        description="Transcribe an audio or video file with Indic-Transcribe. Handles long media by cutting it into windows.",
    )
    def bodhan_transcribe(
        audio_path: str,
        language: str | None = None,
    ) -> TranscribeOutput | ToolErrorOutput:
        source = Path(audio_path)
        if not source.is_file():
            return _error("FILE_NOT_FOUND", f"Audio/video file does not exist: {source}")
        code = normalize_language(language)
        if code:
            support = check_support("transcribe", code)
            if not support["supported"]:
                return _error("LANGUAGE_UNSUPPORTED", support["reason"])

        try:
            duration = probe_duration(source)
            if duration <= 0:
                return _error("INVALID_AUDIO", "Could not determine a positive audio duration.")
            silences = detect_silences(source, duration)
            windows = plan_windows(duration, silences)
            if not windows:
                return _error("INVALID_AUDIO", "No audio windows could be created for this file.")
            texts: list[str] = []
            with tempfile.TemporaryDirectory(prefix="bodhan-transcribe-") as temp_dir:
                for index, (start, end) in enumerate(windows):
                    window = extract_window(source, Path(temp_dir) / f"window-{index}.wav", start, end)
                    texts.append(bodhan.transcribe(window, code).text)
            return {
                "ok": True,
                "text": " ".join(text.strip() for text in texts if text.strip()),
                "windows": len(windows),
                "duration_seconds": duration,
                "cost_inr": stt_cost(duration),
            }
        except Exception as exc:
            return _error("TRANSCRIPTION_FAILED", f"Could not safely split or transcribe audio: {exc}")

    @server.tool(
        name="bodhan_translate",
        description="Translate text with Indic-Translate. Chunks long text internally and reports truncation.",
    )
    def bodhan_translate(
        text: str,
        target_language: str,
        source_language: str | None = None,
        script: str = "native",
    ) -> TranslateOutput | ToolErrorOutput:
        if not text or not text.strip():
            return _error("EMPTY_TEXT", "text must not be empty")
        if is_placeholder_text(text):
            return _error("PLACEHOLDER_TEXT", "Refusing to translate placeholder instructions instead of source text.")
        if script not in {"native", "roman"}:
            return _error("INVALID_SCRIPT", "script must be 'native' or 'roman'.")
        target = normalize_language(target_language)
        support = check_support("translate", target)
        if not support["supported"]:
            return _error("LANGUAGE_UNSUPPORTED", support["reason"])
        source = normalize_language(source_language)
        if source and not check_support("translate", source)["supported"]:
            return _error("LANGUAGE_UNSUPPORTED", f"Source language is not supported: {source_language}")
        try:
            chunks = chunk_text(text, TRANSLATE_CHUNK_CHARS)
            token_caps = translation_token_caps(text)
            results = []
            for chunk, token_cap in zip(chunks, token_caps, strict=True):
                results.append(
                    bodhan.translate(
                        chunk,
                        target or target_language,
                        source,
                        script,
                        token_cap,
                    )
                )
            translated = " ".join(result.text for result in results)
            truncated = any(result.truncated for result in results)
            verification = check_translation(
                text,
                translated,
                target or target_language,
                truncated,
                target_script=script,
            )
            return {
                "ok": True,
                "text": translated,
                "truncated": truncated,
                "verification": verification,
                "chunks": len(results),
                "cost_inr": translate_cost(sum(token_caps)),
            }
        except Exception as exc:
            return _error("TRANSLATION_FAILED", str(exc))

    @server.tool(
        name="bodhan_synthesize",
        description="Synthesize speech with Indic-Speak. Writes audio to a file; never returns inline base64.",
    )
    def bodhan_synthesize(
        text: str,
        language: str,
        voice: str | None = None,
        style: str | None = None,
        output_path: str | None = None,
    ) -> SynthesizeOutput | ToolErrorOutput:
        if not text or not text.strip():
            return _error("EMPTY_TEXT", "text must not be empty")
        code = normalize_language(language)
        support = check_support("speak", code)
        if not support["supported"]:
            return _error("LANGUAGE_UNSUPPORTED", support["reason"])
        if style and style not in STYLES:
            return _error("INVALID_STYLE", f"Unknown style '{style}'. Known styles: {', '.join(STYLES)}")
        try:
            chosen = pick_voice(code, name=voice) if voice else pick_voice(code)
            chunks = chunk_text(text, TTS_CHUNK_CHARS)
            audio_parts = [
                bodhan.synthesize(chunk, code or language, chosen["voice"], style)
                for chunk in chunks
            ]
            audio_bytes, audio_format = _combine_audio(audio_parts)
        except Exception as exc:
            return _error("SYNTHESIS_FAILED", str(exc))

        extension = audio_format
        if output_path:
            artifact = Path(output_path).with_suffix(f".{extension}")
        else:
            temp_dir = tempfile.mkdtemp(prefix="bodhan-speech-")
            artifact = Path(temp_dir) / f"speech.{extension}"
        try:
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(audio_bytes)
        except OSError as exc:
            return _error("ARTIFACT_WRITE_FAILED", f"Could not write speech artifact to {artifact}: {exc}")
        return {
            "ok": True,
            "artifact_path": str(artifact),
            "format": extension,
            "chunks": len(audio_parts),
            "characters": len(text),
            "cost_inr": tts_cost(len(text)),
            "disclaimer": "AI-generated speech.",
        }

    @server.tool(
        name="bodhan_ocr",
        description="Extract Markdown and layout blocks from an image or PDF page with Indic-OCR.",
    )
    def bodhan_ocr(image_path: str) -> OcrOutput | ToolErrorOutput:
        source = Path(image_path)
        if not source.is_file():
            return _error("FILE_NOT_FOUND", f"Image or PDF file does not exist: {source}")
        if source.suffix.lower() == ".pdf":
            try:
                import pypdfium2 as pdfium
            except ImportError:
                return _error("PDF_SUPPORT_NOT_INSTALLED", "pypdfium2 is required to render PDF pages")
            markdown_parts: list[str] = []
            blocks: list[dict[str, Any]] = []
            try:
                pdf = pdfium.PdfDocument(str(source))
                page_count = len(pdf)
                with tempfile.TemporaryDirectory(prefix="bodhan-ocr-") as temp_dir:
                    for index, page in enumerate(pdf):
                        page_image = page.render(scale=2).to_pil()
                        page_file = Path(temp_dir) / f"page-{index + 1}.png"
                        page_image.save(page_file, format="PNG")
                        result = bodhan.ocr(page_file)
                        markdown_parts.append(result.markdown)
                        blocks.extend(result.blocks)
            except Exception as exc:
                return _error("OCR_FAILED", f"Could not render or OCR PDF: {exc}")
            return {
                "ok": True,
                "markdown": "\n\n".join(markdown_parts),
                "blocks": blocks,
                "pages": page_count,
                "cost_inr": ocr_cost(page_count),
            }

        try:
            result = bodhan.ocr(source)
            return {
                "ok": True,
                "markdown": result.markdown,
                "blocks": list(result.blocks),
                "pages": 1,
                "cost_inr": ocr_cost(1),
            }
        except Exception as exc:
            return _error("OCR_FAILED", str(exc))

    @server.tool(
        name="inspect_media",
        description="Inspect a local audio, video, or PDF media file before choosing a tool.",
    )
    def inspect_media_tool(path: str) -> InspectMediaOutput | ToolErrorOutput:
        return inspect_media(path)

    @server.tool(
        name="list_voices",
        description="List supported Indic-Speak voices and styles, optionally for a language and gender.",
    )
    def list_voices_tool(
        language: str | None = None,
        gender: str | None = None,
    ) -> ListVoicesOutput | ToolErrorOutput:
        return list_voices(language, gender)

    @server.tool(
        name="check_language_support",
        description="Check whether a language is supported for transcribe, translate, or speak.",
    )
    def check_language_support_tool(
        capability: str,
        language: str | None = None,
    ) -> LanguageSupportOutput:
        return {"ok": True, **check_support(capability, language)}

    @server.tool(
        name="make_subtitles",
        description="Convert timed transcript segments into an SRT subtitle file string.",
    )
    def make_subtitles_tool(
        source_path: str,
        max_chars_per_line: int = 42,
    ) -> SubtitlesOutput | ToolErrorOutput:
        try:
            segments = _read_timed_segments(source_path)
        except ValueError as exc:
            message = str(exc)
            code, _, detail = message.partition(": ")
            return _error(code, detail or message)
        return make_subtitles(segments, max_chars_per_line)

    return server
