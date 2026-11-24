"""
Inspect media files using ffprobe.

Returns type (audio/video/image/pdf), duration, format, and file size
without loading the file into memory.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}


def get_media_info(file_path: Path) -> dict:
    """Return basic metadata for any media file.

    Returns:
        {
          "type": "audio" | "video" | "image" | "pdf" | "text" | "other",
          "format": str,
          "duration_seconds": float | None,
          "size_bytes": int,
          "path": str,
        }
    """
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        return {
            "type": "pdf",
            "format": "pdf",
            "duration_seconds": None,
            "size_bytes": path.stat().st_size,
            "path": str(path),
        }
    if suffix in _IMAGE_EXTS:
        return {
            "type": "image",
            "format": suffix.lstrip("."),
            "duration_seconds": None,
            "size_bytes": path.stat().st_size,
            "path": str(path),
        }
    if suffix in {".txt", ".md", ".srt", ".vtt"}:
        return {
            "type": "text",
            "format": suffix.lstrip("."),
            "duration_seconds": None,
            "size_bytes": path.stat().st_size,
            "path": str(path),
        }

    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format", "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    fmt = data.get("format", {})
    streams = data.get("streams", [])

    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    media_type = "video" if has_video else ("audio" if has_audio else "other")

    return {
        "type": media_type,
        "format": fmt.get("format_name", "unknown"),
        "duration_seconds": float(fmt["duration"]) if fmt.get("duration") else None,
        "size_bytes": int(fmt.get("size", path.stat().st_size)),
        "path": str(path),
    }
