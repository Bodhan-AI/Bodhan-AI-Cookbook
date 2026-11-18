"""Media utility functions for Bodhan MCP and Agent.

Handles:
- File-reference validation (no base64 in LLM context)
- Media inspection (audio, image, PDF, text)
- Audio chunking into <=30s windows using pure Python standard library wave support
- WAV concatenation and merging
- Multi-page PDF inspection and image preparation
"""

from __future__ import annotations

import base64
import io
import mimetypes
import os
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = None
try:
    import logging
    logger = logging.getLogger("bodhan.media")
except Exception:
    pass

# Safe extensions allowed for processing
AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
DOC_EXTENSIONS = {".pdf", ".txt", ".md"}


class MediaValidationError(ValueError):
    """Raised when media validation fails with actionable error messages."""
    pass


def validate_file_reference(
    file_path: Union[str, Path],
    allowed_extensions: Optional[Set[str]] = None,
    must_exist: bool = True,
) -> Path:
    """Validate that a media reference is a legitimate, accessible file path.
    
    Prevents empty references and invalid extensions.
    """
    if not file_path:
        raise MediaValidationError("File reference path cannot be empty.")

    path = Path(file_path).resolve()

    if must_exist and not path.exists():
        raise FileNotFoundError(
            f"Referenced media file does not exist: {path}. "
            f"Please verify the file path and ensure the file is present on disk."
        )

    if allowed_extensions:
        ext = path.suffix.lower()
        if ext not in allowed_extensions:
            raise MediaValidationError(
                f"Unsupported file format '{ext}' for file {path.name}. "
                f"Supported extensions: {sorted(list(allowed_extensions))}"
            )

    return path


def inspect_audio(path: Path) -> Dict[str, Any]:
    """Inspect audio file attributes (duration, sample rate, channels).
    
    Uses standard library wave for WAV files with graceful metadata fallback.
    """
    stat = path.stat()
    info: Dict[str, Any] = {
        "file_type": "audio",
        "file_name": path.name,
        "file_path": str(path),
        "file_size_bytes": stat.st_size,
        "format": path.suffix.lower().lstrip("."),
        "duration_seconds": 0.0,
        "channels": 1,
        "sample_rate": 16000,
        "requires_chunking": False,
    }

    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as wf:
                channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                framerate = wf.getframerate()
                nframes = wf.getnframes()
                duration = nframes / float(framerate) if framerate > 0 else 0.0

                info.update({
                    "channels": channels,
                    "sample_width_bytes": sample_width,
                    "sample_rate": framerate,
                    "total_frames": nframes,
                    "duration_seconds": round(duration, 3),
                    "requires_chunking": duration > 30.0,
                })
        except Exception as e:
            # Fallback for non-standard wav headers
            info["inspection_warning"] = f"WAV parse note: {e}"
    else:
        # For MP3/FLAC/OGG without external binaries, estimate duration or inspect size
        # Standard speech audio average bitrate ~ 64kbps = 8000 bytes/sec
        estimated_sec = stat.st_size / 8000.0
        info.update({
            "duration_seconds": round(estimated_sec, 2),
            "estimated": True,
            "requires_chunking": estimated_sec > 30.0,
        })

    return info


def inspect_image(path: Path) -> Dict[str, Any]:
    """Inspect image dimensions and format using Pillow."""
    stat = path.stat()
    info: Dict[str, Any] = {
        "file_type": "image",
        "file_name": path.name,
        "file_path": str(path),
        "file_size_bytes": stat.st_size,
        "format": path.suffix.lower().lstrip("."),
        "width": 0,
        "height": 0,
        "mode": "RGB",
    }

    try:
        from PIL import Image
        with Image.open(path) as img:
            info["width"], info["height"] = img.size
            info["mode"] = img.mode
            info["format"] = (img.format or path.suffix.lstrip(".")).lower()
    except Exception as e:
        info["inspection_warning"] = f"Image inspection warning: {e}"

    return info


def inspect_pdf(path: Path) -> Dict[str, Any]:
    """Inspect PDF page count and properties using pypdf."""
    stat = path.stat()
    info: Dict[str, Any] = {
        "file_type": "document",
        "file_name": path.name,
        "file_path": str(path),
        "file_size_bytes": stat.st_size,
        "format": "pdf",
        "page_count": 1,
        "is_encrypted": False,
    }

    try:
        import pypdf
        reader = pypdf.PdfReader(str(path))
        info["page_count"] = len(reader.pages)
        info["is_encrypted"] = reader.is_encrypted
    except Exception as e:
        info["inspection_warning"] = f"PDF inspection warning: {e}"

    return info


def inspect_media(file_path: Union[str, Path]) -> Dict[str, Any]:
    """Unified inspection for audio, image, document, and text files."""
    path = validate_file_reference(file_path, must_exist=True)
    suffix = path.suffix.lower()

    if suffix in AUDIO_EXTENSIONS:
        return inspect_audio(path)
    elif suffix in IMAGE_EXTENSIONS:
        return inspect_image(path)
    elif suffix == ".pdf":
        return inspect_pdf(path)
    else:
        stat = path.stat()
        mime, _ = mimetypes.guess_type(str(path))
        return {
            "file_type": "generic",
            "file_name": path.name,
            "file_path": str(path),
            "file_size_bytes": stat.st_size,
            "mime_type": mime or "application/octet-stream",
            "format": suffix.lstrip("."),
        }


def chunk_audio(
    audio_path: Union[str, Path],
    max_duration_sec: float = 30.0,
    output_dir: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """Split an audio file into chunks of duration <= max_duration_sec.
    
    If the audio is already <= max_duration_sec, returns a single chunk.
    For WAV files, uses frame-accurate slicing via Python's standard `wave` module.
    """
    path = validate_file_reference(audio_path, allowed_extensions=AUDIO_EXTENSIONS, must_exist=True)
    inspection = inspect_audio(path)
    total_duration = inspection["duration_seconds"]

    # Safe margin: cut at 28 seconds if max is 30 to stay strictly within limits
    target_window = min(max_duration_sec, 28.0)

    if total_duration <= max_duration_sec or path.suffix.lower() != ".wav":
        return [{
            "chunk_index": 0,
            "file_path": str(path),
            "start_time_sec": 0.0,
            "end_time_sec": total_duration,
            "duration_sec": total_duration,
        }]

    # Chunking WAV file
    out_dir = Path(output_dir or path.parent / f"{path.stem}_chunks")
    out_dir.mkdir(parents=True, exist_ok=True)

    chunks: List[Dict[str, Any]] = []
    with wave.open(str(path), "rb") as src:
        params = src.getparams()
        framerate = src.getframerate()
        nframes = src.getnframes()
        frames_per_chunk = int(framerate * target_window)

        chunk_idx = 0
        current_frame = 0

        while current_frame < nframes:
            frames_to_read = min(frames_per_chunk, nframes - current_frame)
            data = src.readframes(frames_to_read)
            if not data:
                break

            start_sec = current_frame / float(framerate)
            duration_sec = frames_to_read / float(framerate)
            end_sec = start_sec + duration_sec

            chunk_filename = f"{path.stem}_part{chunk_idx:03d}.wav"
            chunk_file_path = out_dir / chunk_filename

            with wave.open(str(chunk_file_path), "wb") as dst:
                dst.setparams(params)
                dst.setnframes(frames_to_read)
                dst.writeframes(data)

            chunks.append({
                "chunk_index": chunk_idx,
                "file_path": str(chunk_file_path),
                "start_time_sec": round(start_sec, 3),
                "end_time_sec": round(end_sec, 3),
                "duration_sec": round(duration_sec, 3),
            })

            current_frame += frames_to_read
            chunk_idx += 1

    return chunks


def concat_wav_chunks(
    chunk_paths: List[Union[str, Path]],
    output_path: Union[str, Path],
) -> Path:
    """Concatenate multiple WAV audio files into a single unified WAV file."""
    if not chunk_paths:
        raise ValueError("Cannot concatenate empty list of audio chunks.")

    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    first_chunk = Path(chunk_paths[0])
    with wave.open(str(first_chunk), "rb") as first_wf:
        params = first_wf.getparams()

    with wave.open(str(out_file), "wb") as out_wf:
        out_wf.setparams(params)
        for chunk_p in chunk_paths:
            with wave.open(str(chunk_p), "rb") as in_wf:
                out_wf.writeframes(in_wf.readframes(in_wf.getnframes()))

    return out_file


def image_file_to_base64_data_url(image_path: Union[str, Path]) -> str:
    """Read an image file and convert to an RFC 2397 base64 Data URL.
    
    Used strictly inside Bodhan client payload for Indic-OCR; never passed to LLM.
    """
    path = validate_file_reference(image_path, allowed_extensions=IMAGE_EXTENSIONS, must_exist=True)
    suffix = path.suffix.lower().lstrip(".")
    mime_type = f"image/{suffix}" if suffix in ("png", "jpeg", "webp") else "image/jpeg"
    if suffix == "jpg":
        mime_type = "image/jpeg"

    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    return f"data:{mime_type};base64,{encoded}"


def extract_pdf_pages_to_images(
    pdf_path: Union[str, Path],
    output_dir: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """Inspect and extract PDF pages for Indic-OCR processing.
    
    Returns page metadata with either rendered image paths or page indexes.
    """
    path = validate_file_reference(pdf_path, allowed_extensions={".pdf"}, must_exist=True)
    out_dir = Path(output_dir or path.parent / f"{path.stem}_pages")
    out_dir.mkdir(parents=True, exist_ok=True)

    import pypdf
    reader = pypdf.PdfReader(str(path))
    pages: List[Dict[str, Any]] = []

    for idx, page in enumerate(reader.pages):
        page_info = {
            "page_number": idx + 1,
            "pdf_path": str(path),
            "text_extract": page.extract_text() or "",
        }
        # If images are embedded in page, we can also extract them
        if hasattr(page, "images") and len(page.images) > 0:
            first_img = page.images[0]
            img_path = out_dir / f"page_{idx + 1:03d}_{first_img.name}"
            with open(img_path, "wb") as f:
                f.write(first_img.data)
            page_info["image_path"] = str(img_path)

        pages.append(page_info)

    return pages
