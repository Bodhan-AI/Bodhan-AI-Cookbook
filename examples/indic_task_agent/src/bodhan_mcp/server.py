"""MCP server exposing Bodhan model operations as typed tools."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from .audio_plan import detect_silences, extract_window, plan_windows, probe_duration
from .client import BodhanClient
from .fake_client import FakeBodhanClient
from .http_client import HttpBodhanClient
from .languages import (
    STYLES,
    all_voice_names,
    check_support,
    normalize_language,
    pick_voice,
)
from .subtitles import segments_to_srt
from .textutil import chunk_text


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


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
        return {"ok": True, **pick_voice(language, gender)}
    except ValueError as exc:
        return _error("INVALID_VOICE", str(exc))


def make_subtitles(segments: list[dict[str, Any]], max_chars_per_line: int = 42) -> dict[str, Any]:
    if max_chars_per_line < 10:
        return _error("INVALID_SUBTITLE_FORMAT", "max_chars_per_line must be at least 10")
    return {"ok": True, "srt": segments_to_srt(segments, max_chars_per_line)}


def create_server(client: BodhanClient | None = None) -> MCPServer:
    bodhan = client if client is not None else HttpBodhanClient.from_env()
    server = MCPServer(name="bodhan-indic-tools", version="0.1.0")

    @server.tool(
        name="bodhan_transcribe",
        description="Transcribe an audio or video file with Indic-Transcribe. Handles long media by cutting it into windows.",
    )
    def bodhan_transcribe(audio_path: str, language: str | None = None) -> dict[str, Any]:
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
            silences = detect_silences(source, duration)
            windows = plan_windows(duration, silences)
            texts: list[str] = []
            with tempfile.TemporaryDirectory(prefix="bodhan-transcribe-") as temp_dir:
                for index, (start, end) in enumerate(windows):
                    window = extract_window(source, Path(temp_dir) / f"window-{index}.wav", start, end)
                    texts.append(bodhan.transcribe(window, code).text)
            return {"ok": True, "text": " ".join(text.strip() for text in texts if text.strip()), "windows": len(windows)}
        except RuntimeError as exc:
            # A usable response from a real client will supply chunked windows itself.
            # The fake client and some tool-only clients cannot probe arbitrary media.
            try:
                return {"ok": True, "text": bodhan.transcribe(source, code).text, "windows": 1}
            except Exception:
                return _error("TRANSCRIPTION_FAILED", str(exc))
        except Exception as exc:
            return _error("TRANSCRIPTION_FAILED", str(exc))

    @server.tool(
        name="bodhan_translate",
        description="Translate text with Indic-Translate. Chunks long text internally and reports truncation.",
    )
    def bodhan_translate(
        text: str,
        target_language: str,
        source_language: str | None = None,
        script: str = "native",
    ) -> dict[str, Any]:
        if not text or not text.strip():
            return _error("EMPTY_TEXT", "text must not be empty")
        target = normalize_language(target_language)
        support = check_support("translate", target)
        if not support["supported"]:
            return _error("LANGUAGE_UNSUPPORTED", support["reason"])
        source = normalize_language(source_language)
        if source and not check_support("translate", source)["supported"]:
            return _error("LANGUAGE_UNSUPPORTED", f"Source language is not supported: {source_language}")
        try:
            chunks = chunk_text(text, 1200)
            results = [
                bodhan.translate(chunk, target or target_language, source, script)
                for chunk in chunks
            ]
            return {
                "ok": True,
                "text": " ".join(result.text for result in results),
                "truncated": any(result.truncated for result in results),
                "chunks": len(results),
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
    ) -> dict[str, Any]:
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
            chunks = chunk_text(text, 200)
            audio_parts = [
                bodhan.synthesize(chunk, code or language, chosen["voice"], style)
                for chunk in chunks
            ]
        except Exception as exc:
            return _error("SYNTHESIS_FAILED", str(exc))

        extension = audio_parts[0].format if audio_parts else "wav"
        if output_path:
            artifact = Path(output_path)
            if artifact.suffix.lower() not in (".wav", ".mp3"):
                artifact = artifact.with_suffix(f".{extension}")
        else:
            temp_dir = tempfile.mkdtemp(prefix="bodhan-speech-")
            artifact = Path(temp_dir) / f"speech.{extension}"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        with artifact.open("wb") as stream:
            for part in audio_parts:
                stream.write(part.audio_bytes)
        return {
            "ok": True,
            "artifact_path": str(artifact),
            "format": extension,
            "chunks": len(audio_parts),
            "disclaimer": "AI-generated speech.",
        }

    @server.tool(
        name="bodhan_ocr",
        description="Extract Markdown and layout blocks from an image or PDF page with Indic-OCR.",
    )
    def bodhan_ocr(image_path: str) -> dict[str, Any]:
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
            return {"ok": True, "markdown": "\n\n".join(markdown_parts), "blocks": blocks}

        try:
            result = bodhan.ocr(source)
            return {"ok": True, "markdown": result.markdown, "blocks": list(result.blocks)}
        except Exception as exc:
            return _error("OCR_FAILED", str(exc))

    @server.tool(
        name="inspect_media",
        description="Inspect a local audio, video, or PDF media file before choosing a tool.",
    )
    def inspect_media_tool(path: str) -> dict[str, Any]:
        return inspect_media(path)

    @server.tool(
        name="list_voices",
        description="List supported Indic-Speak voices and styles, optionally for a language and gender.",
    )
    def list_voices_tool(language: str | None = None, gender: str | None = None) -> dict[str, Any]:
        return list_voices(language, gender)

    @server.tool(
        name="check_language_support",
        description="Check whether a language is supported for transcribe, translate, or speak.",
    )
    def check_language_support_tool(capability: str, language: str | None = None) -> dict[str, Any]:
        return {"ok": True, **check_support(capability, language)}

    @server.tool(
        name="make_subtitles",
        description="Convert timed transcript segments into an SRT subtitle file string.",
    )
    def make_subtitles_tool(
        segments: list[dict[str, Any]],
        max_chars_per_line: int = 42,
    ) -> dict[str, Any]:
        return make_subtitles(segments, max_chars_per_line)

    return server
