"""
inspect_media tool — returns file metadata without calling any model.

Returns FILE_NOT_FOUND if the file is missing,
UNSUPPORTED_FORMAT if the file cannot be read.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import subprocess
import sys
from pathlib import Path
from typing import Any

from bodhan_core.errors import BodhanError, error_response, success_response
from bodhan_core.errors import ErrorCode
from bodhan_core.langs import detect_script

log = logging.getLogger(__name__)

# MIME-type groups
_AUDIO_TYPES = {
    "audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp3",
    "audio/flac", "audio/ogg", "audio/mp4", "audio/aac",
    "audio/webm",
}
_VIDEO_TYPES = {
    "video/mp4", "video/x-msvideo", "video/quicktime",
    "video/webm", "video/x-matroska",
}
_IMAGE_TYPES = {
    "image/png", "image/jpeg", "image/jpg", "image/tiff",
    "image/bmp", "image/gif", "image/webp",
}
_TEXT_TYPES = {
    "text/plain", "text/markdown", "text/csv",
    "application/json", "text/html",
}


def _run_ffprobe(path: Path) -> dict[str, Any] | None:
    """Return ffprobe JSON for the file, or None on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_streams", "-show_format",
                str(path),
            ],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except Exception as exc:  # noqa: BLE001
        log.debug("ffprobe failed: %s", exc, file=sys.stderr)
        return None


def _probe_audio_video(path: Path) -> dict[str, Any]:
    """Extract audio/video metadata via ffprobe."""
    info = _run_ffprobe(path)
    out: dict[str, Any] = {}
    if not info:
        return out
    fmt = info.get("format", {})
    out["duration_seconds"] = float(fmt.get("duration", 0)) or None
    out["size_bytes"] = int(fmt.get("size", path.stat().st_size))
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "audio":
            out["sample_rate"] = int(stream.get("sample_rate", 0)) or None
            out["channels"] = stream.get("channels")
            out["audio_codec"] = stream.get("codec_name")
        if stream.get("codec_type") == "video":
            out["width"] = stream.get("width")
            out["height"] = stream.get("height")
            out["video_codec"] = stream.get("codec_name")
    return out


def _probe_image(path: Path) -> dict[str, Any]:
    """Extract image dimensions using Pillow if available, else ffprobe."""
    out: dict[str, Any] = {"size_bytes": path.stat().st_size}
    try:
        from PIL import Image  # type: ignore[import]
        with Image.open(path) as img:
            out["width"], out["height"] = img.size
            out["image_format"] = img.format
    except ImportError:
        info = _run_ffprobe(path)
        if info:
            for stream in info.get("streams", []):
                if stream.get("codec_type") == "video":
                    out["width"] = stream.get("width")
                    out["height"] = stream.get("height")
    except Exception as exc:  # noqa: BLE001
        log.debug("Image probe failed: %s", exc)
    return out


def _probe_pdf(path: Path) -> dict[str, Any]:
    """Get PDF page count using pypdf if available."""
    out: dict[str, Any] = {"size_bytes": path.stat().st_size}
    try:
        import pypdf  # type: ignore[import]
        reader = pypdf.PdfReader(str(path))
        out["page_count"] = len(reader.pages)
    except ImportError:
        try:
            # Fallback: pdfinfo CLI
            result = subprocess.run(
                ["pdfinfo", str(path)],
                capture_output=True, timeout=15,
            )
            for line in result.stdout.decode(errors="replace").splitlines():
                if line.lower().startswith("pages:"):
                    out["page_count"] = int(line.split(":", 1)[1].strip())
                    break
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        log.debug("PDF probe failed: %s", exc)
    return out


def _probe_text(path: Path) -> dict[str, Any]:
    """Read text file and return char_count + script_hint."""
    out: dict[str, Any] = {"size_bytes": path.stat().st_size}
    try:
        raw = path.read_bytes()
        # Detect encoding
        encoding = "utf-8"
        try:
            import chardet  # type: ignore[import]
            det = chardet.detect(raw[:4096])
            encoding = det.get("encoding") or "utf-8"
            out["encoding_hint"] = encoding
        except ImportError:
            out["encoding_hint"] = "utf-8"
        text = raw.decode(encoding, errors="replace")
        out["char_count"] = len(text)
        candidates = detect_script(text[:500])
        if candidates:
            out["script_hint"] = candidates[0]
    except Exception as exc:  # noqa: BLE001
        log.debug("Text probe failed: %s", exc)
    return out


def register(mcp, client, workspace, cost_meter) -> None:  # noqa: ANN001
    @mcp.tool()
    async def inspect_media(file_path: str) -> dict:  # type: ignore[return]
        """Inspect a media file and return its type, duration, dimensions, \
page count, size, and a script/language hint. No model cost. Use this before \
any other tool to understand what you are working with. Returns FILE_NOT_FOUND \
if the file does not exist, UNSUPPORTED_FORMAT if the file cannot be read."""
        try:
            resolved = workspace.resolve(file_path)
        except BodhanError as exc:
            return exc.to_response()
        except Exception as exc:  # noqa: BLE001
            return error_response(
                ErrorCode.FILE_NOT_FOUND,
                f"Cannot resolve path: {exc}",
                "Check that the file path is correct and within the workspace.",
            )

        if not resolved.exists():
            return error_response(
                ErrorCode.FILE_NOT_FOUND,
                f"File not found: {file_path}",
                "Provide a path to an existing file.",
            )

        try:
            mime, _ = mimetypes.guess_type(str(resolved))
            suffix = resolved.suffix.lower()

            # PDF special-case (mimetypes often wrong)
            if suffix == ".pdf":
                mime = "application/pdf"

            data: dict[str, Any] = {
                "mime_type": mime or "application/octet-stream",
                "size_bytes": resolved.stat().st_size,
            }

            if mime in _AUDIO_TYPES or (
                suffix in {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus"}
            ):
                data["file_type"] = "audio"
                data.update(_probe_audio_video(resolved))

            elif mime in _VIDEO_TYPES or suffix in {".mp4", ".mkv", ".avi", ".mov", ".webm"}:
                data["file_type"] = "video"
                data.update(_probe_audio_video(resolved))

            elif mime == "application/pdf":
                data["file_type"] = "pdf"
                data.update(_probe_pdf(resolved))

            elif mime in _IMAGE_TYPES or suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
                data["file_type"] = "image"
                data.update(_probe_image(resolved))

            elif mime in _TEXT_TYPES or suffix in {".txt", ".md", ".csv", ".json", ".html"}:
                data["file_type"] = "text"
                data.update(_probe_text(resolved))

            else:
                return error_response(
                    ErrorCode.UNSUPPORTED_FORMAT,
                    f"Cannot determine file type for: {file_path} (mime={mime})",
                    "Provide an audio, video, image, PDF, or text file.",
                )

            return success_response(data, cost_inr=0.0, cached=False)

        except Exception as exc:  # noqa: BLE001
            log.error("inspect_media error: %s", exc, exc_info=True)
            return error_response(
                ErrorCode.UNSUPPORTED_FORMAT,
                f"Could not read file: {exc}",
                "Ensure the file is not corrupted and is a supported format.",
            )
