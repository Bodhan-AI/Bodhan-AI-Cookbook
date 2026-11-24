"""Helper tools for Bodhan MCP Server: inspect_media and read_text_file.

These tools allow the Task Agent to inspect files inside the designated --files directory
while strictly preventing path traversal attacks.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple

import pymupdf
from pydantic import BaseModel

from bodhan_mcp.tools.transcribe import get_ffmpeg_binaries, probe_audio_duration

# Allowed extensions
ALLOWED_TEXT_EXTENSIONS = {".txt", ".md", ".srt", ".csv"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi"}


def resolve_secure_path(
    raw_path: str, base_dir: Optional[Path] = None
) -> Tuple[Optional[Path], Optional[str]]:
    """Resolves path and enforces that it resides strictly within base_dir.

    Rejects path traversal (e.g. '../', absolute paths pointing outside base_dir).
    """
    raw_path_str = raw_path.strip()
    if not raw_path_str:
        return None, "Path cannot be empty."

    env_base = os.getenv("BODHAN_FILES_DIR")
    target_base = (base_dir or (Path(env_base) if env_base else Path.cwd())).resolve()

    candidate = Path(raw_path_str)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        resolved = (target_base / candidate).resolve()

    try:
        resolved.relative_to(target_base)
    except ValueError:
        return (
            None,
            f"Path traversal rejected: '{raw_path}' is outside permitted directory '{target_base}'.",
        )

    if not resolved.exists():
        return None, f"File not found: '{raw_path}'."

    if not resolved.is_file():
        return None, f"Path is not a regular file: '{raw_path}'."

    return resolved, None


class MediaInspectionResult(BaseModel):
    success: bool
    file_path: str
    media_type: str = ""
    file_size_bytes: int = 0
    duration_seconds: Optional[float] = None
    page_count: Optional[int] = None
    format_name: Optional[str] = None
    error: Optional[str] = None


class ReadTextResult(BaseModel):
    success: bool
    file_path: str
    text: str = ""
    character_count: int = 0
    truncated: bool = False
    error: Optional[str] = None


def inspect_media_file(file_path: str, base_dir: Optional[Path] = None) -> MediaInspectionResult:
    """Inspects media file and returns type, size, duration (ffprobe) or page count (PDF/images)."""
    resolved_path, err = resolve_secure_path(file_path, base_dir)
    if err or not resolved_path:
        return MediaInspectionResult(
            success=False,
            file_path=file_path,
            error=err or "Invalid file path.",
        )

    size = resolved_path.stat().st_size
    suffix = resolved_path.suffix.lower()

    if suffix == ".pdf":
        try:
            doc = pymupdf.open(str(resolved_path))
            pages = doc.page_count
            doc.close()
            return MediaInspectionResult(
                success=True,
                file_path=str(resolved_path),
                media_type="pdf",
                file_size_bytes=size,
                page_count=pages,
                format_name="pdf",
            )
        except Exception as e:
            return MediaInspectionResult(
                success=False,
                file_path=str(resolved_path),
                media_type="pdf",
                file_size_bytes=size,
                error=f"Failed to inspect PDF: {str(e)}",
            )

    if suffix in IMAGE_EXTENSIONS:
        return MediaInspectionResult(
            success=True,
            file_path=str(resolved_path),
            media_type="image",
            file_size_bytes=size,
            page_count=1,
            format_name=suffix.lstrip("."),
        )

    if suffix in AUDIO_EXTENSIONS or suffix in VIDEO_EXTENSIONS:
        media_type = "audio" if suffix in AUDIO_EXTENSIONS else "video"
        _, ffprobe_bin = get_ffmpeg_binaries()
        if not ffprobe_bin:
            return MediaInspectionResult(
                success=False,
                file_path=str(resolved_path),
                media_type=media_type,
                file_size_bytes=size,
                error="ffprobe executable not found on system PATH.",
            )

        duration, probe_err = probe_audio_duration(resolved_path, ffprobe_bin)
        if probe_err or duration is None:
            return MediaInspectionResult(
                success=False,
                file_path=str(resolved_path),
                media_type=media_type,
                file_size_bytes=size,
                error=probe_err.message if probe_err else "Failed to probe duration.",
            )

        return MediaInspectionResult(
            success=True,
            file_path=str(resolved_path),
            media_type=media_type,
            file_size_bytes=size,
            duration_seconds=round(duration, 3),
            format_name=suffix.lstrip("."),
        )

    return MediaInspectionResult(
        success=False,
        file_path=str(resolved_path),
        file_size_bytes=size,
        error=f"Unsupported media format '{suffix}'. Supported: PDF, images, audio, video.",
    )


def read_text_file_content(
    file_path: str, max_chars: int = 10000, base_dir: Optional[Path] = None
) -> ReadTextResult:
    """Reads text file content up to max_chars for .txt, .md, .srt, .csv."""
    resolved_path, err = resolve_secure_path(file_path, base_dir)
    if err or not resolved_path:
        return ReadTextResult(
            success=False,
            file_path=file_path,
            error=err or "Invalid file path.",
        )

    suffix = resolved_path.suffix.lower()
    if suffix not in ALLOWED_TEXT_EXTENSIONS:
        return ReadTextResult(
            success=False,
            file_path=str(resolved_path),
            error=f"File extension '{suffix}' not allowed. Allowed: {sorted(list(ALLOWED_TEXT_EXTENSIONS))}",
        )

    try:
        content = resolved_path.read_text(encoding="utf-8")
        truncated = False
        if len(content) > max_chars:
            content = content[:max_chars]
            truncated = True

        return ReadTextResult(
            success=True,
            file_path=str(resolved_path),
            text=content,
            character_count=len(content),
            truncated=truncated,
        )
    except Exception as e:
        return ReadTextResult(
            success=False,
            file_path=str(resolved_path),
            error=f"Failed to read file: {str(e)}",
        )
