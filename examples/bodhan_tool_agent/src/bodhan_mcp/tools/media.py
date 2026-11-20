"""Media helper tools: inspect_media, chunk_audio, and get_voice_library."""

import mimetypes
from pathlib import Path

import pymupdf  # PyMuPDF

from bodhan_mcp import config, errors
from bodhan_mcp.schemas import ChunkAudioOut, InspectOut, VoiceLibraryOut
from bodhan_mcp.utils import audio


def inspect_media(path: str) -> InspectOut | errors.ToolErrorPayload:
    """Inspect local media file and extract metadata without invoking external models.

    Absorbs local file inspection. Never calls models. Free of charge.
    Returns InspectOut with kind, duration_s, pages, format, size_mb.
    Returns ToolErrorPayload on error (e.g. FILE_NOT_FOUND).
    """
    file_path = Path(path)
    if not file_path.exists():
        return errors.make_tool_error(
            code=errors.FILE_NOT_FOUND,
            message=f"Media file not found: {file_path}",
            hint="Verify input path exists and is accessible.",
            details={"path": str(file_path)},
        )

    size_mb = round(file_path.stat().st_size / (1024 * 1024), 3)
    ext = file_path.suffix.lower().lstrip(".")
    mime, _ = mimetypes.guess_type(str(file_path))
    mime = mime or ""

    kind = "unknown"
    duration_s: float | None = None
    pages: int | None = None
    media_format = ext or "unknown"

    # Audio detection
    audio_extensions = {"wav", "mp3", "m4a", "flac", "ogg", "aac", "wma"}
    if ext in audio_extensions or "audio" in mime:
        kind = "audio"
        try:
            probe_meta = audio.probe(file_path)
            duration_s = probe_meta.get("duration_s")
            media_format = probe_meta.get("format", ext)
        except Exception:
            # Fallback for formats probe might fail on
            duration_s = None

    # PDF detection
    elif ext == "pdf" or "pdf" in mime:
        kind = "pdf"
        media_format = "pdf"
        try:
            with pymupdf.open(str(file_path)) as doc:
                pages = len(doc)
        except Exception:
            pages = None

    # Image detection
    elif ext in {"png", "jpg", "jpeg", "webp", "bmp", "tiff"} or "image" in mime:
        kind = "image"
        pages = 1
        media_format = ext

    # Video detection
    elif ext in {"mp4", "mov", "avi", "mkv", "webm"} or "video" in mime:
        kind = "video"
        media_format = ext

    # Text detection
    elif ext in {"txt", "md", "json", "srt", "csv", "tsv"} or "text" in mime:
        kind = "text"
        media_format = ext

    return InspectOut(
        kind=kind,  # type: ignore
        duration_s=duration_s,
        pages=pages,
        format=media_format,
        size_mb=size_mb,
    )


def chunk_audio(
    path: str,
    out_dir: str | None = None,
) -> ChunkAudioOut | errors.ToolErrorPayload:
    """Split an audio file into pause-bounded chunks <= 30s.

    Absorbs pause-based chunk boundaries for callers that wish to manage chunks.
    Free of charge.
    Returns ChunkAudioOut with chunk paths and time offsets.
    Errors: FILE_NOT_FOUND, UNREADABLE_MEDIA.
    """
    file_path = Path(path)
    if not file_path.exists():
        return errors.make_tool_error(
            code=errors.FILE_NOT_FOUND,
            message=f"Audio file not found: {file_path}",
            hint="Check file path.",
            details={"path": str(file_path)},
        )

    output_dir = Path(out_dir) if out_dir else file_path.parent / f"_{file_path.stem}_chunks"

    try:
        chunks = audio.chunk_at_pauses(file_path, out_dir=output_dir)
        return ChunkAudioOut(chunks=chunks)
    except FileNotFoundError:
        return errors.make_tool_error(
            code=errors.FILE_NOT_FOUND,
            message=f"Audio file not found: {file_path}",
            hint="Check file path.",
            details={"path": str(file_path)},
        )
    except Exception as exc:
        return errors.make_tool_error(
            code=errors.UNREADABLE_MEDIA,
            message=f"Unreadable audio media: {exc}",
            hint="Ensure audio is a valid WAV/MP3/M4A file.",
            details={"path": str(file_path), "error": str(exc)},
        )


def get_voice_library() -> VoiceLibraryOut:
    """Discover available voices per supported Indic language for speech synthesis.

    Never fails. Free of charge.
    """
    return VoiceLibraryOut(voices=config.VOICE_LIBRARY)
