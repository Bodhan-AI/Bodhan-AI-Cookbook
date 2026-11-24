"""Files in, files out: input resolution, media kind detection, output locations, previews."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from .errors import ErrorCode, ToolError

AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".wma", ".amr", ".webm"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".3gp", ".wmv", ".flv", ".mpeg", ".mpg"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
PDF_EXT = {".pdf"}
TEXT_EXT = {".txt", ".md", ".markdown", ".srt", ".vtt", ".csv", ".tsv", ".json", ".html", ".htm", ".rtf", ""}

PREVIEW_CHARS = 300


def resolve_input(path: str) -> Path:
    """Resolve a caller-supplied path to an existing file, with actionable errors."""
    if not isinstance(path, str) or not path.strip():
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "path must be a non-empty string.", hint="Pass a local file path.")
    if path.startswith(("data:", "http://", "https://")):
        raise ToolError(ErrorCode.INVALID_ARGUMENT, "Media must be passed as a local file path, not a URL or data URI.",
                        hint="Save the file locally and pass its path.")
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    p = p.resolve()
    allowed = [Path(d).expanduser().resolve() for d in os.environ.get("BODHAN_ALLOWED_DIRS", "").split(os.pathsep) if d]
    if allowed and not any(p == d or d in p.parents for d in allowed):
        raise ToolError(ErrorCode.INVALID_ARGUMENT, f"{path} is outside the allowed directories.",
                        hint="BODHAN_ALLOWED_DIRS restricts which files this server may read.")
    if not p.exists():
        raise ToolError(ErrorCode.FILE_NOT_FOUND, f"File not found: {path}",
                        hint="Check the path; relative paths resolve against the server's working directory "
                             f"({Path.cwd()}).")
    if p.is_dir():
        raise ToolError(ErrorCode.INVALID_ARGUMENT, f"{path} is a directory, not a file.",
                        hint="Pass one file at a time; list the directory yourself.")
    if p.stat().st_size == 0:
        raise ToolError(ErrorCode.UNSUPPORTED_MEDIA, f"{path} is empty (0 bytes).")
    return p


def media_kind(path: Path) -> str:
    """audio | video | image | pdf | text | unknown, from the extension then magic bytes."""
    ext = path.suffix.lower()
    with path.open("rb") as fh:
        head = fh.read(16)
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG") or head[:3] == b"\xff\xd8\xff":
        return "image"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "audio"
    for kind, exts in (("pdf", PDF_EXT), ("image", IMAGE_EXT), ("audio", AUDIO_EXT), ("video", VIDEO_EXT)):
        if ext in exts:
            return kind
    if ext in TEXT_EXT:
        return "text"
    return "unknown"


def output_dir(out_dir: str | None) -> Path:
    base = Path(out_dir or os.environ.get("BODHAN_WORKSPACE") or "bodhan_workspace").expanduser()
    if not base.is_absolute():
        base = Path.cwd() / base
    base.mkdir(parents=True, exist_ok=True)
    return base.resolve()


def artifact_path(directory: Path, source: Path | str, suffix: str, *, key: str = "") -> Path:
    """Deterministic, collision-free output name: <source stem>.<short hash><suffix>."""
    from .langs import language_names

    parts = Path(str(source)).name.split(".")
    base = re.sub(r"[^\w-]+", "_", parts[0])[:60] or "output"
    codes = set(language_names())
    # Carry a language tag (e.g. ".en" from a translation) unless the new suffix names its own language.
    carried = [] if any(t in codes for t in suffix.split(".")) else [t for t in parts[1:-1] if t in codes]
    stem = ".".join([base, *carried[-1:]])
    digest = hashlib.sha256(f"{source}|{key}".encode()).hexdigest()[:8]
    return directory / f"{stem}.{digest}{suffix}"


def preview(text: str, n: int = PREVIEW_CHARS) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def read_text_file(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16"):
        try:
            text = raw.decode(enc)
            if enc == "utf-16" and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
                continue
            return text
        except UnicodeDecodeError:
            continue
    raise ToolError(ErrorCode.UNSUPPORTED_MEDIA, f"{path.name} is not UTF-8/UTF-16 text.",
                    hint="Convert the file to UTF-8, or pass it to ocr_document if it is a scanned image.")
