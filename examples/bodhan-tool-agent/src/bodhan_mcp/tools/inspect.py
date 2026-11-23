from __future__ import annotations

import mimetypes
import re
import subprocess
import wave
from pathlib import Path

from ..models import ErrorCode, ToolError, failure, success
from ..workspace import Workspace

_AUDIO_SUFFIXES = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}
_VIDEO_SUFFIXES = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm"}
_TEXT_SUFFIXES = {".md", ".markdown", ".srt", ".txt", ".vtt"}
_MIME_OVERRIDES = {
    ".md": "text/markdown",
    ".srt": "application/x-subrip",
    ".wav": "audio/wav",
}


def audio_duration_seconds(path: Path) -> float | None:
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as audio:
                rate = audio.getframerate()
                return audio.getnframes() / rate if rate > 0 else None
        except (EOFError, wave.Error):
            return None

    try:
        import imageio_ffmpeg
    except ImportError:
        raise ToolError(
            ErrorCode.UNSUPPORTED_FORMAT,
            "Media duration metadata requires the bundled FFmpeg utility.",
            hint="Install the project dependencies again, including imageio-ffmpeg.",
        ) from None

    try:
        result = subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(
            ErrorCode.UPSTREAM_TIMEOUT,
            f"Reading duration metadata for {path.name!r} exceeded 30 seconds.",
        ) from exc
    except OSError as exc:
        raise ToolError(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"Could not read duration metadata for {path.name!r}.",
            hint="Check that the file is a supported media format.",
        ) from exc

    output = result.stderr.decode("utf-8", errors="replace")
    match = re.search(
        r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)",
        output,
    )
    if match is None:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def inspect_media(workspace: Workspace, file_path: str) -> dict:
    try:
        path = workspace.resolve_input(file_path)
        suffix = path.suffix.lower()
        mime_type = (
            _MIME_OVERRIDES.get(suffix)
            or mimetypes.guess_type(path.name, strict=False)[0]
            or "application/octet-stream"
        )
        if suffix == ".pdf":
            file_type = "pdf"
        elif suffix in _AUDIO_SUFFIXES:
            file_type = "audio"
        elif suffix in _VIDEO_SUFFIXES:
            file_type = "video"
        elif suffix in _TEXT_SUFFIXES:
            file_type = "text"
        elif mime_type.startswith("image/"):
            file_type = "image"
        else:
            file_type = "other"

        data: dict[str, object] = {
            "path": str(path),
            "name": path.name,
            "file_type": file_type,
            "extension": suffix,
            "size_bytes": path.stat().st_size,
            "mime_type": mime_type,
            "line_count": None,
            "duration_seconds": None,
            "page_count": None,
            "metadata_method": None,
        }

        if suffix in _TEXT_SUFFIXES:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise ToolError(
                    ErrorCode.UNSUPPORTED_FORMAT,
                    f"Could not decode {path.name!r} as UTF-8 text.",
                    hint="Save the file as UTF-8 and try again.",
                ) from exc
            data["line_count"] = len(text.splitlines())
            data["metadata_method"] = "UTF-8 line count"
        elif suffix == ".pdf":
            contents = path.read_bytes()
            page_count = len(re.findall(rb"/Type\s*/Page\b", contents))
            if page_count:
                data["page_count"] = page_count
                data["metadata_method"] = "PDF page-object count estimate"
        elif suffix in _AUDIO_SUFFIXES | _VIDEO_SUFFIXES:
            duration = audio_duration_seconds(path)
            if duration is not None:
                data["duration_seconds"] = round(duration, 3)
                data["metadata_method"] = (
                    "WAV frame count" if suffix == ".wav" else "FFmpeg container duration"
                )
            else:
                data["metadata_method"] = "Media duration unavailable"

        return success(data)
    except ToolError as err:
        return failure(err)
    except OSError as exc:
        return failure(
            ToolError(
                ErrorCode.INTERNAL,
                f"Could not inspect {file_path!r}: {exc.strerror or exc}.",
            )
        )