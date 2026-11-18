"""Media Helper Tools for Bodhan MCP.

Provides inspection and chunking helper tools with strongly typed I/O
and actionable diagnostic errors.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from bodhan_mcp.media_utils import (
    AUDIO_EXTENSIONS,
    chunk_audio,
    inspect_media,
    validate_file_reference,
)

logger = logging.getLogger("bodhan.tools.media")


def inspect_media_file(file_path: Union[str, Path]) -> Dict[str, Any]:
    """Inspect a media file and return its properties.
    
    Args:
        file_path: Path to the media file (audio, image, PDF, or text).
        
    Returns:
        Structured dictionary with format, dimensions, duration, channels, and page counts.
    """
    return inspect_media(file_path)


def chunk_audio_file(
    audio_path: Union[str, Path],
    max_duration_sec: float = 30.0,
    output_dir: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Split an audio file into sub-30-second windows for Indic-Transcribe processing.
    
    Args:
        audio_path: Path to the audio file.
        max_duration_sec: Maximum window duration (default: 30.0s).
        output_dir: Optional directory where chunks will be written.
        
    Returns:
        Dictionary containing list of chunks, total duration, and chunk count.
    """
    chunks = chunk_audio(
        audio_path=audio_path,
        max_duration_sec=max_duration_sec,
        output_dir=output_dir,
    )
    total_dur = sum(c["duration_sec"] for c in chunks)

    return {
        "original_file": str(Path(audio_path).resolve()),
        "chunks": chunks,
        "total_chunks": len(chunks),
        "total_duration_seconds": round(total_dur, 3),
        "max_window_seconds": max_duration_sec,
    }
