#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from ..models import (
    estimate_transcribe_cost,
    estimate_ocr_cost,
    ErrorCode,
)
from ..media import inspect_audio_video, inspect_pdf, inspect_image


def _error(
    code: ErrorCode, message: str, retryable: bool = False,
    details: dict | None = None,
) -> dict:
    """Return a structured error response."""
    return {
        "error": code.value,
        "message": message,
        "code": code.value,
        "retryable": retryable,
        "details": details or {},
    }


async def inspect_media(file_path: str) -> dict:
    path = Path(file_path)
    if not path.exists():
        return _error(
            ErrorCode.FILE_NOT_FOUND,
            f"File not found: {file_path}",
            retryable=False,
        )
    if path.is_dir():
        return _error(
            ErrorCode.INVALID_INPUT,
            f"Path is a directory, expected a file: {file_path}",
            retryable=False,
        )

    suffix = path.suffix.lower()
    format_name = suffix.lstrip(".") or "unknown"

    audio_exts = (
        ".wav", ".mp3", ".m4a", ".flac", ".ogg", ".mp4", ".mov", ".avi", ".mkv"
    )
    if suffix in audio_exts:
        try:
            info = inspect_audio_video(str(path))
        except Exception as e:
            return _error(
                ErrorCode.MEDIA_PROCESSING_ERROR,
                f"Failed to inspect audio/video: {e}",
                retryable=False,
            )
        duration = info.get("duration_sec", 0)
        min_cost = estimate_transcribe_cost(duration)
        return {
            "format": format_name,
            "duration_sec": duration,
            "bitrate": info.get("bitrate", 0),
            "sample_rate": info.get("sample_rate"),
            "channels": info.get("channels"),
            "codec": info.get("audio_codec"),
            "language": None,
            "min_cost_inr": round(min_cost, 4),
            "supported_for_transcribe": True,
            "supported_for_translate": False,
            "supported_for_ocr": False,
            "supported_for_tts": False,
        }

    elif suffix == ".pdf":
        try:
            info = inspect_pdf(str(path))
        except Exception as e:
            return _error(
                ErrorCode.MEDIA_PROCESSING_ERROR,
                f"Failed to inspect PDF: {e}",
                retryable=False,
            )
        pages = info.get("pages", 0)
        min_cost = estimate_ocr_cost(pages)
        return {
            "format": "pdf",
            "duration_sec": 0,
            "bitrate": 0,
            "sample_rate": None,
            "channels": None,
            "pages": pages,
            "language": None,
            "min_cost_inr": round(min_cost, 4),
            "supported_for_transcribe": False,
            "supported_for_translate": True,
            "supported_for_ocr": True,
            "supported_for_tts": False,
        }

    elif suffix in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"):
        try:
            info = inspect_image(str(path))
        except Exception as e:
            return _error(
                ErrorCode.MEDIA_PROCESSING_ERROR,
                f"Failed to inspect image: {e}",
                retryable=False,
            )
        min_cost = estimate_ocr_cost(1)
        return {
            "format": format_name,
            "duration_sec": 0,
            "bitrate": 0,
            "sample_rate": None,
            "channels": None,
            "dimensions": (info.get("width", 0), info.get("height", 0)),
            "language": None,
            "min_cost_inr": round(min_cost, 4),
            "supported_for_transcribe": False,
            "supported_for_translate": True,
            "supported_for_ocr": True,
            "supported_for_tts": False,
        }

    elif suffix in (".txt", ".md"):
        return {
            "format": format_name,
            "duration_sec": 0,
            "bitrate": 0,
            "sample_rate": None,
            "channels": None,
            "characters": len(path.read_text(encoding="utf-8", errors="ignore")),
            "language": None,
            "min_cost_inr": 0.0,
            "supported_for_transcribe": False,
            "supported_for_translate": True,
            "supported_for_ocr": False,
            "supported_for_tts": True,
        }

    else:
        return _error(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"Unsupported file format: {suffix}",
            retryable=False,
            details={
                "supported_extensions": sorted([
                    *audio_exts, ".pdf", ".png", ".jpg", ".jpeg", ".webp",
                    ".bmp", ".tiff", ".txt", ".md",
                ])
            },
        )


__all__ = ["inspect_media"]
