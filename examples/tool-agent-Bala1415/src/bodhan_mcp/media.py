"""
Media utilities — audio chunking, PDF-to-image conversion, format detection.

All media manipulation happens here so tools and the agent stay clean.
Requires: ffmpeg (system), pymupdf, pillow, pydub.
"""

from __future__ import annotations

import logging
import math
import mimetypes
import os
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_AUDIO_CHUNK_SECONDS = 30  # Indic-Transcribe limit
SUPPORTED_AUDIO_FORMATS = {".wav", ".mp3", ".flac", ".ogg"}
SUPPORTED_IMAGE_FORMATS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif"}
PDF_RENDER_DPI = 200  # DPI for PDF → image conversion


# ---------------------------------------------------------------------------
# File Inspection
# ---------------------------------------------------------------------------
def inspect_file(file_path: str) -> dict[str, Any]:
    """
    Inspect a file and return metadata: type, size, format-specific info.

    Returns:
        dict with 'path', 'exists', 'size_bytes', 'mime_type', 'category',
        and format-specific fields like 'duration_seconds', 'page_count', etc.
    """
    path = Path(file_path)
    info: dict[str, Any] = {
        "path": str(path.resolve()),
        "name": path.name,
        "exists": path.exists(),
        "size_bytes": 0,
        "mime_type": "unknown",
        "category": "unknown",
        "extension": path.suffix.lower(),
    }

    if not path.exists():
        return info

    info["size_bytes"] = path.stat().st_size
    mime, _ = mimetypes.guess_type(str(path))
    info["mime_type"] = mime or "application/octet-stream"

    ext = path.suffix.lower()

    # Categorize
    if ext in SUPPORTED_AUDIO_FORMATS:
        info["category"] = "audio"
        info["duration_seconds"] = get_audio_duration(str(path))
    elif ext in SUPPORTED_IMAGE_FORMATS:
        info["category"] = "image"
        info.update(_get_image_info(str(path)))
    elif ext == ".pdf":
        info["category"] = "document"
        info["page_count"] = get_pdf_page_count(str(path))
    elif ext in {".txt", ".md", ".csv", ".json", ".srt"}:
        info["category"] = "text"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            info["char_count"] = len(text)
            info["line_count"] = text.count("\n") + 1
        except Exception:
            pass
    elif ext in {".mp4", ".avi", ".mov", ".mkv", ".webm"}:
        info["category"] = "video"

    return info


def _get_image_info(image_path: str) -> dict[str, Any]:
    """Get image dimensions without loading full image into memory."""
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            return {"width": img.width, "height": img.height, "mode": img.mode}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Audio Utilities
# ---------------------------------------------------------------------------
def get_audio_duration(audio_path: str) -> float:
    """Get audio duration in seconds using ffprobe or fallback."""
    # Try ffprobe first
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-show_entries",
                "format=duration", "-of", "csv=p=0", audio_path,
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        pass

    # Fallback: parse WAV header
    path = Path(audio_path)
    if path.suffix.lower() == ".wav":
        try:
            return _wav_duration(str(path))
        except Exception:
            pass

    # Fallback: estimate from file size (rough: 16kHz 16-bit mono ≈ 32KB/s)
    try:
        size = Path(audio_path).stat().st_size
        return size / 32_000  # Very rough estimate
    except (FileNotFoundError, OSError):
        return 0.0


def _wav_duration(wav_path: str) -> float:
    """Parse WAV header to get duration without external tools."""
    with open(wav_path, "rb") as f:
        f.read(4)  # RIFF
        f.read(4)  # file size
        f.read(4)  # WAVE
        f.read(4)  # fmt
        fmt_size = struct.unpack("<I", f.read(4))[0]
        f.read(2)  # audio format
        channels = struct.unpack("<H", f.read(2))[0]
        sample_rate = struct.unpack("<I", f.read(4))[0]
        byte_rate = struct.unpack("<I", f.read(4))[0]
        f.read(2)  # block_align
        bits_per_sample = struct.unpack("<H", f.read(2))[0]
        if fmt_size > 16:
            f.read(fmt_size - 16)  # skip rest of fmt
        # Find data chunk
        while True:
            chunk_id = f.read(4)
            if len(chunk_id) < 4:
                break
            chunk_size_data = f.read(4)
            if len(chunk_size_data) < 4:
                break
            chunk_size = struct.unpack("<I", chunk_size_data)[0]
            if chunk_id == b"data":
                if byte_rate > 0:
                    return chunk_size / byte_rate
                elif sample_rate > 0 and channels > 0 and bits_per_sample > 0:
                    return chunk_size / (sample_rate * channels * bits_per_sample // 8)
                return 0.0
            f.seek(chunk_size, 1)
    return 0.0


def chunk_audio(
    audio_path: str,
    max_seconds: float = MAX_AUDIO_CHUNK_SECONDS,
    output_dir: str | None = None,
) -> list[dict[str, Any]]:
    """
    Split an audio file into chunks of max_seconds duration.

    Uses ffmpeg if available, otherwise falls back to pydub.

    Args:
        audio_path: Path to the audio file.
        max_seconds: Maximum chunk duration in seconds.
        output_dir: Directory to write chunks. Uses temp dir if None.

    Returns:
        List of dicts with 'path', 'start_seconds', 'end_seconds', 'duration_seconds'.
    """
    path = Path(audio_path)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    total_duration = get_audio_duration(str(path))

    # If short enough, return as single chunk
    if total_duration <= max_seconds:
        return [{
            "path": str(path.resolve()),
            "start_seconds": 0.0,
            "end_seconds": total_duration,
            "duration_seconds": total_duration,
            "index": 0,
        }]

    out_dir = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="bodhan_chunks_"))
    out_dir.mkdir(parents=True, exist_ok=True)

    num_chunks = math.ceil(total_duration / max_seconds)
    chunks = []

    for i in range(num_chunks):
        start = i * max_seconds
        duration = min(max_seconds, total_duration - start)
        chunk_path = out_dir / f"chunk_{i:04d}.wav"

        try:
            # Try ffmpeg
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(path),
                    "-ss", str(start), "-t", str(duration),
                    "-ar", "16000", "-ac", "1",  # 16kHz mono for transcription
                    "-acodec", "pcm_s16le",
                    str(chunk_path),
                ],
                capture_output=True, timeout=30, check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            # Fallback to pydub
            _chunk_with_pydub(str(path), str(chunk_path), start, duration)

        chunks.append({
            "path": str(chunk_path),
            "start_seconds": round(start, 2),
            "end_seconds": round(start + duration, 2),
            "duration_seconds": round(duration, 2),
            "index": i,
        })

    logger.info(f"Split {path.name} into {len(chunks)} chunks of ≤{max_seconds}s")
    return chunks


def _chunk_with_pydub(input_path: str, output_path: str, start_sec: float, duration_sec: float):
    """Fallback chunking using pydub (slower, still needs ffmpeg for some formats)."""
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_file(input_path)
        start_ms = int(start_sec * 1000)
        end_ms = int((start_sec + duration_sec) * 1000)
        chunk = audio[start_ms:end_ms]
        # Convert to 16kHz mono WAV
        chunk = chunk.set_frame_rate(16000).set_channels(1).set_sample_width(2)
        chunk.export(output_path, format="wav")
    except ImportError:
        raise RuntimeError(
            "Neither ffmpeg nor pydub is available. Install ffmpeg: "
            "winget install -e --id Gyan.FFmpeg"
        )


# ---------------------------------------------------------------------------
# PDF Utilities
# ---------------------------------------------------------------------------
def get_pdf_page_count(pdf_path: str) -> int:
    """Get the number of pages in a PDF."""
    try:
        import fitz  # pymupdf
        doc = fitz.open(pdf_path)
        count = len(doc)
        doc.close()
        return count
    except Exception as e:
        logger.warning(f"Failed to get PDF page count: {e}")
        return 0


def pdf_to_images(
    pdf_path: str,
    output_dir: str | None = None,
    dpi: int = PDF_RENDER_DPI,
) -> list[dict[str, Any]]:
    """
    Convert each page of a PDF to a PNG image.

    Args:
        pdf_path: Path to the PDF file.
        output_dir: Directory to write page images. Uses temp dir if None.
        dpi: Resolution for rendering.

    Returns:
        List of dicts with 'path', 'page_number', 'width', 'height'.
    """
    path = Path(pdf_path)
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    try:
        import fitz  # pymupdf
    except ImportError:
        raise RuntimeError("pymupdf is required for PDF processing. Install with: pip install pymupdf")

    out_dir = Path(output_dir) if output_dir else Path(tempfile.mkdtemp(prefix="bodhan_pages_"))
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(path))
    pages = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        # Render at specified DPI
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)

        img_path = out_dir / f"page_{page_num + 1:04d}.png"
        pix.save(str(img_path))

        pages.append({
            "path": str(img_path),
            "page_number": page_num + 1,
            "width": pix.width,
            "height": pix.height,
        })

    doc.close()
    logger.info(f"Converted {path.name}: {len(pages)} pages → PNG at {dpi} DPI")
    return pages


# ---------------------------------------------------------------------------
# Format Detection
# ---------------------------------------------------------------------------
def detect_file_type(file_path: str) -> str:
    """
    Detect the type category of a file.

    Returns one of: 'audio', 'image', 'document', 'text', 'video', 'unknown'.
    """
    info = inspect_file(file_path)
    return info.get("category", "unknown")


def is_audio(file_path: str) -> bool:
    return Path(file_path).suffix.lower() in SUPPORTED_AUDIO_FORMATS


def is_image(file_path: str) -> bool:
    return Path(file_path).suffix.lower() in SUPPORTED_IMAGE_FORMATS


def is_pdf(file_path: str) -> bool:
    return Path(file_path).suffix.lower() == ".pdf"


def is_text(file_path: str) -> bool:
    return Path(file_path).suffix.lower() in {".txt", ".md", ".csv", ".json", ".srt"}
