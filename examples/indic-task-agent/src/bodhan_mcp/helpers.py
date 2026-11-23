"""
Media Processing Helpers and Model Limit Handlers for Bodhan MCP Server.

Provides:
1. Audio chunking (sub-30s windows for Indic-Transcribe)
2. Audio concatenation and LUFS loudness normalization (for Indic-Speak)
3. Sentence-boundary text chunker (for Indic-Speak TTS limits)
4. Comprehensive media inspector (inspect_media) for audio, images, and PDFs
5. Multi-page PDF page extractor for Indic-OCR
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from PIL import Image

try:
    import pypdf
except ImportError:
    pypdf = None

from bodhan_mcp.config import (
    CACHE_DIR,
    TARGET_LOUDNESS_LUFS,
    TRANSCRIBE_MAX_WINDOW_SEC,
    TTS_MAX_CHUNK_CHARS,
)

logger = logging.getLogger("bodhan.helpers")


# ---------------------------------------------------------------------------
# 1. Media Inspection (inspect_media)
# ---------------------------------------------------------------------------
def inspect_media(file_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Inspects any media file (audio, image, PDF, video) and returns technical metadata.
    Does not use GPU or call any AI models (Zero cost: ₹0.00).
    """
    path = Path(file_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    suffix = path.suffix.lower()
    file_size_bytes = path.stat().st_size

    result: Dict[str, Any] = {
        "file_name": path.name,
        "file_path": str(path),
        "file_size_bytes": file_size_bytes,
        "extension": suffix,
    }

    # Audio formats
    audio_extensions = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac", ".opus"}
    # Image formats
    image_extensions = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}
    # Text & document formats
    text_extensions = {".txt", ".md", ".csv", ".srt", ".vtt", ".json"}
    doc_extensions = {".pdf"}

    if suffix in audio_extensions:
        result["media_type"] = "audio"
        audio_meta = _probe_audio(path)
        result.update(audio_meta)
    elif suffix in image_extensions:
        result["media_type"] = "image"
        image_meta = _probe_image(path)
        result.update(image_meta)
    elif suffix in doc_extensions:
        result["media_type"] = "pdf"
        pdf_meta = _probe_pdf(path)
        result.update(pdf_meta)
    elif suffix in text_extensions:
        result["media_type"] = "text"
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            result["character_count"] = len(content)
            result["line_count"] = len(content.splitlines())
            # Provide text content (capped at 16k chars if extremely large)
            result["text_content"] = content if len(content) <= 16000 else content[:16000] + "\n...[truncated]"
        except Exception as e:
            result["error"] = f"Failed to read text: {e}"
    else:
        # Fallback probe with ffprobe in case it's video or unknown media
        probe_data = _run_ffprobe(path)
        if probe_data and "streams" in probe_data:
            has_video = any(s.get("codec_type") == "video" for s in probe_data["streams"])
            has_audio = any(s.get("codec_type") == "audio" for s in probe_data["streams"])
            result["media_type"] = "video" if has_video else ("audio" if has_audio else "generic_media")
            fmt = probe_data.get("format", {})
            result["duration_seconds"] = round(float(fmt.get("duration", 0.0)), 2)
        else:
            result["media_type"] = "unknown"

    return result


def _probe_image(path: Path) -> Dict[str, Any]:
    """Probes image dimensions, mode, and format using PIL."""
    try:
        with Image.open(path) as img:
            return {
                "width": img.width,
                "height": img.height,
                "format": img.format or path.suffix.lstrip(".").upper(),
                "mode": img.mode,
            }
    except Exception as e:
        logger.warning(f"PIL failed to probe image {path}: {e}")
        return {"error": f"Failed to read image metadata: {str(e)}"}


def _probe_pdf(path: Path) -> Dict[str, Any]:
    """Probes PDF page count using pypdf."""
    page_count = 1
    if pypdf:
        try:
            reader = pypdf.PdfReader(str(path))
            page_count = len(reader.pages)
        except Exception as e:
            logger.warning(f"pypdf failed to read {path}: {e}")
    return {"page_count": page_count}


def _probe_audio(path: Path) -> Dict[str, Any]:
    """Probes audio file for duration, sample rate, channels using wave or ffprobe."""
    # Fast path: standard WAV
    try:
        with wave.open(str(path), "rb") as w:
            rate = w.getframerate()
            frames = w.getnframes()
            channels = w.getnchannels()
            duration = float(frames) / float(rate) if rate > 0 else 0.0
            return {
                "duration_seconds": round(duration, 2),
                "sample_rate": rate,
                "channels": channels,
                "format": "wav",
            }
    except Exception:
        pass

    # Standard path: ffprobe
    probe = _run_ffprobe(path)
    if probe and "format" in probe:
        fmt = probe["format"]
        streams = probe.get("streams", [])
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})
        duration = float(fmt.get("duration", 0.0))
        return {
            "duration_seconds": round(duration, 2),
            "sample_rate": int(audio_stream.get("sample_rate", 0)),
            "channels": int(audio_stream.get("channels", 0)),
            "format": fmt.get("format_name", path.suffix.lstrip(".")),
        }

    return {"duration_seconds": 0.0}


def _run_ffprobe(path: Path) -> Optional[Dict[str, Any]]:
    """Runs ffprobe on a media file and returns parsed JSON metadata."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(proc.stdout)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 2. Audio Chunking for 30s Limit (Indic-Transcribe)
# ---------------------------------------------------------------------------
def chunk_audio_if_needed(
    audio_path: Union[str, Path],
    max_chunk_sec: float = 28.0,
    chunk_dir: Optional[Union[str, Path]] = None,
) -> List[Path]:
    """
    Ensures input audio adheres to Indic-Transcribe's 30.0s window limit.
    If the audio exceeds max_chunk_sec, it splits the file into sequential chunks.
    Returns list of chunk file paths.
    """
    path = Path(audio_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")

    meta = _probe_audio(path)
    duration = meta.get("duration_seconds", 0.0)

    # If within limit, return directly without modification
    if duration <= max_chunk_sec and duration > 0:
        return [path]

    out_dir = Path(chunk_dir or (CACHE_DIR / "chunks"))
    out_dir.mkdir(parents=True, exist_ok=True)

    stem = path.stem
    chunk_paths: List[Path] = []
    
    # Use ffmpeg segment muxer for clean, artifact-free sub-second splitting
    out_pattern = str(out_dir / f"{stem}_part%03d.wav")
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(path),
        "-f", "segment",
        "-segment_time", str(max_chunk_sec),
        "-c:a", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        out_pattern,
    ]

    try:
        subprocess.run(cmd, capture_output=True, check=True)
        # Collect produced chunks sorted by segment index
        produced = sorted(out_dir.glob(f"{stem}_part*.wav"))
        if produced:
            return produced
    except Exception as e:
        logger.warning(f"ffmpeg segmentation failed, falling back to original: {e}")

    return [path]


# ---------------------------------------------------------------------------
# 3. Sentence-Boundary Text Chunker (for Indic-Speak TTS)
# ---------------------------------------------------------------------------
def split_text_for_tts(text: str, max_chars: int = TTS_MAX_CHUNK_CHARS) -> List[str]:
    """
    Splits long text into natural sentence chunks respecting Indic and Latin punctuation:
    - Purna Viram (।)
    - Periods (.), Question marks (?), Exclamation marks (!)
    - Newlines (\n)
    
    Prevents sentences from being clipped mid-word and ensures Indic-Speak inputs
    remain within recommended sentence length (~400 characters).
    """
    text = text.strip()
    if not text:
        return []

    if len(text) <= max_chars:
        return [text]

    # Regex matching sentence boundaries followed by whitespace
    sentence_regex = re.compile(r'(?<=[।\.\?\!\n])\s+')
    raw_sentences = sentence_regex.split(text)

    chunks: List[str] = []
    current_chunk = ""

    for s in raw_sentences:
        s = s.strip()
        if not s:
            continue

        # If a single sentence is abnormally long, split on commas or spaces
        if len(s) > max_chars:
            words = s.split(" ")
            sub_chunk = ""
            for w in words:
                if len(sub_chunk) + len(w) + 1 <= max_chars:
                    sub_chunk = f"{sub_chunk} {w}".strip()
                else:
                    if sub_chunk:
                        chunks.append(sub_chunk)
                    sub_chunk = w
            if sub_chunk:
                chunks.append(sub_chunk)
            continue

        if len(current_chunk) + len(s) + 1 <= max_chars:
            current_chunk = f"{current_chunk} {s}".strip() if current_chunk else s
        else:
            chunks.append(current_chunk)
            current_chunk = s

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


# ---------------------------------------------------------------------------
# 4. Audio Concatenation & LUFS Normalization (for Indic-Speak)
# ---------------------------------------------------------------------------
def concatenate_audio_clips(
    clip_paths: List[Union[str, Path]],
    output_path: Union[str, Path],
    target_lufs: float = TARGET_LOUDNESS_LUFS,
) -> Path:
    """
    Concatenates multiple audio clips into a single continuous file and applies
    EBU R128 loudness normalization (default: -16 LUFS) for consistent volume.
    """
    valid_clips = [Path(p).resolve() for p in clip_paths if Path(p).exists()]
    if not valid_clips:
        raise ValueError("No valid audio clips provided for concatenation.")

    out = Path(output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    if len(valid_clips) == 1:
        # Single clip: normalize and write directly
        _normalize_audio(valid_clips[0], out, target_lufs)
        return out

    # Multiple clips: build concat file for ffmpeg concat demuxer
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as concat_file:
        for clip in valid_clips:
            # Escape single quotes for ffmpeg concat demuxer
            safe_clip = str(clip).replace("'", "'\\''")
            concat_file.write(f"file '{safe_clip}'\n")
        concat_list_path = concat_file.name

    try:
        # Concatenate and apply loudness normalization in one ffmpeg pass
        loudnorm_filter = f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list_path,
            "-af", loudnorm_filter,
            "-ar", "24000",
            "-c:a", "pcm_s16le",
            str(out),
        ]
        subprocess.run(cmd, capture_output=True, check=True)
    finally:
        if os.path.exists(concat_list_path):
            try:
                os.remove(concat_list_path)
            except Exception:
                pass

    return out


def _normalize_audio(input_path: Path, output_path: Path, target_lufs: float):
    """Applies EBU R128 loudnorm filter to a single audio file."""
    loudnorm_filter = f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11"
    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(input_path),
        "-af", loudnorm_filter,
        "-ar", "24000",
        "-c:a", "pcm_s16le",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except Exception as e:
        logger.warning(f"Audio normalization failed, copying file directly: {e}")
        shutil.copyfile(input_path, output_path)


# ---------------------------------------------------------------------------
# 5. Multi-page PDF Page Extractor (for Indic-OCR)
# ---------------------------------------------------------------------------
def extract_pdf_pages_as_images(
    pdf_path: Union[str, Path],
    output_dir: Optional[Union[str, Path]] = None,
    dpi: int = 150,
) -> List[Path]:
    """
    Extracts every page of a multi-page PDF as an individual PNG image
    so Indic-OCR can process each page sequentially.
    """
    path = Path(pdf_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    out_dir = Path(output_dir or (CACHE_DIR / "pdf_pages" / path.stem))
    out_dir.mkdir(parents=True, exist_ok=True)

    prefix = str(out_dir / "page")
    cmd = [
        "pdftoppm",
        "-png",
        "-r", str(dpi),
        str(path),
        prefix,
    ]
    try:
        subprocess.run(cmd, capture_output=True, check=True)
        # Collect and sort generated pages
        pages = sorted(out_dir.glob("page-*.png"))
        if pages:
            return pages
    except Exception as e:
        logger.warning(f"pdftoppm failed on {path}: {e}")

    return []
