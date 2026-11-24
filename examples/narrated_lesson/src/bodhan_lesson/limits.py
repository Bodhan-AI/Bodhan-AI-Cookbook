"""Input size and page-count guards for the narrated-lesson pipeline."""

from __future__ import annotations

from pathlib import Path

from bodhan_lesson.constants import IMAGE_SUFFIXES

# Assignment range is 5–30 pages; reject only when count goes above 30.
MAX_PAGES = 30
MAX_INPUT_BYTES = 30 * 1024 * 1024  # 30 MB


def input_size_bytes(path: Path) -> int:
    """Return total bytes for a PDF file or an image folder."""
    if path.is_file():
        return path.stat().st_size
    if path.is_dir():
        total = 0
        for child in path.iterdir():
            if child.is_file() and child.suffix.lower() in IMAGE_SUFFIXES:
                total += child.stat().st_size
        return total
    return 0


def assert_input_size_ok(path: Path) -> None:
    """Reject oversized PDFs or image folders before heavy work."""
    size = input_size_bytes(path)
    if size > MAX_INPUT_BYTES:
        mb = size / (1024 * 1024)
        limit_mb = MAX_INPUT_BYTES / (1024 * 1024)
        raise ValueError(
            f"Input is {mb:.1f} MB; max allowed is {limit_mb:.0f} MB"
        )


def assert_page_count_ok(page_count: int) -> None:
    """Reject chapters longer than MAX_PAGES."""
    if page_count > MAX_PAGES:
        raise ValueError(
            f"Found {page_count} pages; max allowed is {MAX_PAGES}"
        )
