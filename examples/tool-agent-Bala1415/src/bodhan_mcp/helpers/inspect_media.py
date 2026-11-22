"""
Media inspection helper — returns file metadata without API calls.

Exposed as an MCP tool so the agent/planner can inspect files before choosing tools.
"""

from __future__ import annotations

from typing import Any

from bodhan_mcp.media import inspect_file


def inspect_media(file_path: str) -> dict[str, Any]:
    """
    Inspect a media file and return structured metadata.

    Returns file type, size, duration (audio), page count (PDF),
    dimensions (image), character count (text), etc.

    Args:
        file_path: Path to the file to inspect.

    Returns:
        dict with metadata fields depending on file type.
    """
    return inspect_file(file_path)
