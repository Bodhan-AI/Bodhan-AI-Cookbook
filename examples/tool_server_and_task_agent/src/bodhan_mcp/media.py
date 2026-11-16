"""Local media plumbing: probing, WAV chunking, PDF rendering, inspection.

Everything here runs locally with the standard library, ffmpeg (when
present) and PyMuPDF. No model is called from this module.

Rules the tools rely on:
- STT accepts at most 30 seconds of audio per request, and refuses
  M4A/AAC/WebM/AMR with HTTP 415, so non-WAV audio is converted to WAV
  with ffmpeg first, and long audio is split into <=30s WAV chunks.
- TTS wants a sentence or two per request (~30s of speech), so long text
  is split into sentence chunks before synthesis and the WAVs are joined.
- OCR takes PNG/JPEG page images only, so PDFs are rendered to images.
"""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import struct
import subprocess
import wave
from pathlib import Path

from .errors import FILE_NOT_FOUND, UNSUPPORTED_MEDIA, ToolError

MAX_AUDIO_SECONDS = 30.0
AUDIO_EXTS = {".wav", ".flac", ".ogg", ".mp3"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg"}
STT_NATIVE_EXTS = {".wav", ".flac", ".ogg", ".mp3"}


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def require_file(path: str | Path) -> Path:
    p = Path(path)
    if not p.exists():
        raise ToolError(
            FILE_NOT_FOUND,
            f"File not found: {path}",
            hint="Check the path and pass it again.",
        )
    if not p.is_file():
        raise ToolError(
            FILE_NOT_FOUND,
            f"Not a file: {path}",
            hint="Pass a file path, not a directory.",
        )
    return p


def wav_duration(path: str | Path) -> float:
    """Duration of a WAV file in seconds (stdlib wave, no ffmpeg)."""
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def probe_audio(path: str | Path) -> dict:
    """Return format/duration info for an audio file.

    WAV is read with the standard library. Anything else goes through
    ffprobe; if ffprobe is missing, the duration is reported as unknown
    and the caller must convert with ffmpeg first.
    """
    p = require_file(path)
    ext = p.suffix.lower()
    if ext == ".wav":
        with wave.open(str(p), "rb") as wf:
            return {
                "format": "wav",
                "duration_seconds": round(wf.getnframes() / wf.getframerate(), 3),
                "sample_rate": wf.getframerate(),
                "channels": wf.getnchannels(),
                "sample_width": wf.getsampwidth(),
            }
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return {"format": ext.lstrip("."), "duration_seconds": None,
                "error": "ffprobe not found; install ffmpeg to probe this format"}
    out = subprocess.run(
        [ffprobe, "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", str(p)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if out.returncode != 0:
        raise ToolError(
            UNSUPPORTED_MEDIA,
            f"Could not probe audio file: {path}",
            hint="Send WAV, FLAC, OGG or MP3.",
        )
    import json

    data = json.loads(out.stdout or "{}")
    fmt = data.get("format", {})
    duration = float(fmt.get("duration", 0) or 0)
    stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {}
    )
    return {
        "format": ext.lstrip("."),
        "duration_seconds": round(duration, 3),
        "sample_rate": int(stream.get("sample_rate", 0) or 0),
        "channels": int(stream.get("channels", 0) or 0),
    }


def ensure_wav(path: str | Path, workdir: str | Path) -> Path:
    """Return a WAV version of ``path`` (converting with ffmpeg if needed).

    STT refuses M4A/AAC/WebM/AMR outright, so anything that is not already
    WAV is converted to 16 kHz mono WAV before it is sent.
    """
    p = require_file(path)
    if p.suffix.lower() == ".wav":
        return p
    if p.suffix.lower() not in AUDIO_EXTS:
        raise ToolError(
            UNSUPPORTED_MEDIA,
            f"Unsupported audio format: {p.suffix} ({p.name})",
            hint="Send WAV, FLAC, OGG or MP3.",
        )
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ToolError(
            UNSUPPORTED_MEDIA,
            f"Cannot convert {p.suffix} without ffmpeg, and the STT API "
            "refuses this container.",
            hint="Install ffmpeg or send a WAV file.",
        )
    out = Path(workdir) / f"{p.stem}.converted.wav"
    if not out.exists():
        subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-i", str(p),
             "-ac", "1", "-ar", "16000", str(out)],
            capture_output=True, text=True, timeout=120, check=True,
        )
    return out


def split_wav(path: str | Path, max_seconds: float, workdir: str | Path) -> list[Path]:
    """Split a WAV into chunks of at most ``max_seconds`` seconds.

    Chunks are cut on frame boundaries. Returns the chunk paths in order.
    A file that already fits returns a one-element list with the original.
    """
    p = require_file(path)
    with wave.open(str(p), "rb") as wf:
        params = wf.getparams()
        total_frames = wf.getnframes()
        rate = wf.getframerate()
        frames_per_chunk = int(max_seconds * rate)
        if total_frames <= frames_per_chunk:
            return [p]
        chunks: list[Path] = []
        wf.rewind()
        idx = 0
        while True:
            frames = wf.readframes(frames_per_chunk)
            if not frames:
                break
            chunk = Path(workdir) / f"{p.stem}.chunk{idx:02d}.wav"
            with wave.open(str(chunk), "wb") as out:
                out.setparams(params)
                out.writeframes(frames)
            chunks.append(chunk)
            idx += 1
        return chunks


def concat_wavs(paths: list[str | Path], out_path: str | Path) -> Path:
    """Concatenate WAV files with identical parameters into one WAV."""
    if not paths:
        raise ToolError("invalid_input", "No WAV files to concatenate.")
    first = require_file(paths[0])
    with wave.open(str(first), "rb") as wf:
        params = wf.getparams()
    # Compare everything except nframes: chunks legitimately differ in length.
    format_key = (params.nchannels, params.sampwidth, params.framerate,
                  params.comptype, params.compname)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as writer:
        writer.setparams(params)
        for path in paths:
            p = require_file(path)
            with wave.open(str(p), "rb") as reader:
                rp = reader.getparams()
                if (rp.nchannels, rp.sampwidth, rp.framerate,
                        rp.comptype, rp.compname) != format_key:
                    raise ToolError(
                        UNSUPPORTED_MEDIA,
                        f"WAV parameters differ, cannot join: {p.name}",
                        hint="Synthesise every chunk with the same voice "
                        "and settings, then join them.",
                    )
                writer.writeframes(reader.readframes(reader.getnframes()))
    return out


def synth_tone_wav(
    out_path: str | Path,
    duration_seconds: float,
    freq: float = 440.0,
    sample_rate: int = 24000,
) -> Path:
    """Write a mono PCM16 WAV of a pure tone (mock TTS output).

    Used only by the mock client so the pipeline (chunking, joining,
    back-transcription) works end to end without a real API key.
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = max(1, int(duration_seconds * sample_rate))
    frames = b"".join(
        struct.pack(
            "<h", int(12000 * math.sin(2 * math.pi * freq * i / sample_rate))
        )
        for i in range(n)
    )
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(frames)
    return out


def render_pdf_pages(path: str | Path, dpi: int = 200) -> list[Path]:
    """Render each page of a PDF to a PNG (PyMuPDF)."""
    p = require_file(path)
    try:
        import fitz
    except ImportError as exc:
        raise ToolError(
            UNSUPPORTED_MEDIA,
            "PDF input needs PyMuPDF, which is not installed.",
            hint="pip install PyMuPDF, or send page images (PNG/JPEG).",
        ) from exc
    doc = fitz.open(str(p))
    pages: list[Path] = []
    workdir = Path(os.environ.get("TMPDIR", "/tmp")) / f"bodhan_pdf_{p.stem}"
    workdir.mkdir(parents=True, exist_ok=True)
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=dpi)
        out = workdir / f"{p.stem}.page{i + 1:02d}.png"
        pix.save(str(out))
        pages.append(out)
    return pages


def pdf_page_count(path: str | Path) -> int:
    p = require_file(path)
    try:
        import fitz
    except ImportError:
        return -1
    with fitz.open(str(p)) as doc:
        return doc.page_count


def inspect_file(path: str | Path) -> dict:
    """Describe a file for the planner: kind, format, size, duration/pages.

    Local only, no model call, no cost.
    """
    p = require_file(path)
    ext = p.suffix.lower()
    info: dict = {
        "path": str(p),
        "name": p.name,
        "size_bytes": p.stat().st_size,
        "extension": ext,
    }
    if ext in AUDIO_EXTS:
        info["kind"] = "audio"
        try:
            info.update(probe_audio(p))
        except ToolError as exc:
            info["error"] = exc.message
    elif ext in IMAGE_EXTS:
        info["kind"] = "image"
        try:
            from PIL import Image

            with Image.open(p) as im:
                info["width"], info["height"] = im.size
                info["format"] = im.format
        except Exception:
            info["error"] = "could not read image dimensions"
    elif ext == ".pdf":
        info["kind"] = "document"
        info["format"] = "pdf"
        count = pdf_page_count(p)
        info["pages"] = count if count >= 0 else "unknown (PyMuPDF missing)"
    elif ext in {".txt", ".md", ".srt", ".json", ".jsonl"}:
        info["kind"] = "text"
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
            info["chars"] = len(text)
            info["lines"] = text.count("\n") + 1
        except Exception:
            info["error"] = "could not read text"
    elif ext in {".mp4", ".mov", ".mkv", ".webm"}:
        info["kind"] = "video"
        info["format"] = ext.lstrip(".")
        info["note"] = "video is not processed; only its metadata is read"
    else:
        info["kind"] = "unknown"
        info["note"] = f"extension {ext} has no handler"
    return info
