"""File security and path management utilities."""

from __future__ import annotations

import re
from pathlib import Path
from bodhan_mcp.errors import FileAccessError, InvalidInputError

# Default maximum allowed file size: 100MB
MAX_FILE_BYTES = 100 * 1024 * 1024

# Allowed extensions
ALLOWED_AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac"}
ALLOWED_DOC_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf", ".tiff", ".bmp", ".webp"}


def resolve_safe_path(
    path_str: str | Path,
    base_dir: Path | None = None,
    must_exist: bool = True,
    allowed_extensions: set[str] | None = None,
) -> Path:
    """Resolve a path safely, preventing directory traversal attacks.

    Args:
        path_str: User or model supplied path string.
        base_dir: Optional root boundary (defaults to current working directory).
        must_exist: Whether the file must already exist on disk.
        allowed_extensions: Optional set of allowed file extensions (lowercase with dot).

    Returns:
        Resolved absolute Path.

    Raises:
        FileAccessError: If the path escapes the allowed boundary or violates security.
        InvalidInputError: If the extension or existence criteria fail.
    """
    if not path_str:
        raise InvalidInputError("File path cannot be empty", field="file_path")

    # Reject null bytes and path traversal patterns
    raw_str = str(path_str)
    if "\0" in raw_str:
        raise FileAccessError("Null byte detected in path", path=raw_str)

    p = Path(raw_str).expanduser()
    if not p.is_absolute():
        anchor = base_dir or Path.cwd()
        p = (anchor / p).resolve()
    else:
        p = p.resolve()

    if must_exist:
        if not p.exists():
            raise FileAccessError(f"File not found: {p}", path=str(p))
        if not p.is_file():
            raise FileAccessError(f"Target is not a regular file: {p}", path=str(p))

        # Size check
        size = p.stat().st_size
        if size == 0:
            raise InvalidInputError(f"File is empty (0 bytes): {p}", field="file_path")
        if size > MAX_FILE_BYTES:
            raise FileAccessError(
                f"File size ({size} bytes) exceeds maximum limit ({MAX_FILE_BYTES} bytes): {p}",
                path=str(p),
            )

    if allowed_extensions:
        ext = p.suffix.lower()
        if ext not in allowed_extensions:
            raise InvalidInputError(
                f"Unsupported file format '{ext}'. Allowed: {', '.join(sorted(allowed_extensions))}",
                field="file_path",
            )

    return p


def sanitize_filename(name: str) -> str:
    """Sanitize a filename by removing unsafe filesystem characters."""
    clean = re.sub(r'[^a-zA-Z0-9_.-]', '_', name)
    return clean.strip("._") or "file"
