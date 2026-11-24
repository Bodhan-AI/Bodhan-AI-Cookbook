"""Audio, PDF and image plumbing: ffmpeg for audio, the wave module for slicing and joining, PyMuPDF for pages."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import wave
from pathlib import Path

import pymupdf

from .bodhan import ToolFailure

AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".amr", ".wma", ".webm"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".3gp"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif"}
TEXT_EXT = {".txt", ".md", ".markdown", ".srt", ".vtt", ".csv", ".tsv", ".json", ".html", ".htm"}

MAX_WINDOW_S = 28.0  # Indic-Transcribe rejects audio over 30 s; keep a margin.
MIN_WINDOW_S = 8.0  # Do not cut at a silence earlier than this; it would make many tiny requests.
MAX_IMAGE_SIDE = 2400


def kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    for kind, exts in (("audio", AUDIO_EXT), ("video", VIDEO_EXT), ("image", IMAGE_EXT), ("text", TEXT_EXT)):
        if ext in exts:
            return kind
    return "pdf" if ext == ".pdf" else "unknown"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    if not shutil.which(args[0]):
        raise ToolFailure("ffmpeg_missing", f"{args[0]} is not installed.", hint="Install ffmpeg (brew install ffmpeg, or apt-get install ffmpeg).")
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode:
        raise ToolFailure("media_error", f"{args[0]} failed: {proc.stderr.strip()[-300:]}", hint="Check that the file is valid audio or video.")
    return proc


def probe(path: Path) -> dict:
    out = json.loads(_run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)]).stdout)
    types = {s.get("codec_type") for s in out.get("streams", [])}
    return {"duration_s": float(out.get("format", {}).get("duration") or 0), "has_audio": "audio" in types, "has_video": "video" in types}


def to_wav(src: Path, dst: Path, rate: int = 16000) -> None:
    """Any audio or video to mono PCM16 WAV, the format Indic-Transcribe documents."""
    _run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", str(dst)])


def wav_duration(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def silences(wav: Path, noise_db: int = -35, min_s: float = 0.3) -> list[tuple[float, float]]:
    err = _run(["ffmpeg", "-nostdin", "-v", "info", "-i", str(wav), "-af", f"silencedetect=n={noise_db}dB:d={min_s}", "-f", "null", "-"]).stderr
    starts = [max(0.0, float(x)) for x in re.findall(r"silence_start: (-?[\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    ends += [wav_duration(wav)] * (len(starts) - len(ends))  # silence that runs to the end has no end marker
    return list(zip(starts, ends))


def plan_cuts(duration: float, silent: list[tuple[float, float]], max_len: float = MAX_WINDOW_S, min_len: float = MIN_WINDOW_S) -> list[tuple[float, float]]:
    """Split [0, duration] into windows of at most max_len, cutting in the middle of the last silence that fits."""
    windows, start = [], 0.0
    while duration - start > max_len:
        mids = [(a + b) / 2 for a, b in silent if start + min_len < (a + b) / 2 <= start + max_len]
        cut = mids[-1] if mids else start + max_len  # shortcut: hard cut mid-word when there is no pause; upgrade to VAD if this hurts accuracy
        windows.append((start, cut))
        start = cut
    if duration - start > 0.05:
        windows.append((start, duration))
    return windows


def is_silent(start: float, end: float, silent: list[tuple[float, float]]) -> bool:
    return any(a <= start + 0.05 and b >= end - 0.05 for a, b in silent)


def slice_wav(src: Path, start: float, end: float, dst: Path) -> None:
    with wave.open(str(src)) as w:
        rate = w.getframerate()
        w.setpos(min(int(start * rate), w.getnframes()))
        frames = w.readframes(int((end - start) * rate))
        params = w.getparams()
    with wave.open(str(dst), "wb") as out:
        out.setparams(params)
        out.writeframes(frames)


def concat_wavs(srcs: list[Path], dst: Path, gap_s: float = 0.15) -> None:
    """Join WAVs of one format, with a short pause between them so sentences do not run together."""
    params = None
    with wave.open(str(dst), "wb") as out:
        for i, src in enumerate(srcs):
            with wave.open(str(src)) as w:
                if params is None:
                    params = w.getparams()
                    out.setparams(params)
                elif w.getparams()[:3] != params[:3]:
                    raise ToolFailure("media_error", f"{src.name} has a different audio format from the other chunks.")
                if i:
                    out.writeframes(b"\x00" * int(gap_s * params.framerate) * params.sampwidth * params.nchannels)
                out.writeframes(w.readframes(w.getnframes()))


def encode(src_wav: Path, dst: Path, comment: str) -> None:
    """Write the final file (WAV or MP3, by extension) with an AI-generated label in its metadata."""
    _run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src_wav), "-metadata", f"comment={comment}", "-metadata", "title=AI-generated speech", str(dst)])


def parse_pages(spec: str | None, count: int) -> list[int]:
    if not spec:
        return list(range(1, count + 1))
    pages: list[int] = []
    for part in spec.replace(" ", "").split(","):
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ToolFailure("invalid_pages", f"Cannot read page range {spec!r}.", hint="Use a form like '1-3,5'.")
        a, b = int(m[1]), int(m[2] or m[1])
        if not 1 <= a <= b <= count:
            raise ToolFailure("invalid_pages", f"Pages {part} are outside 1-{count}.", hint=f"The document has {count} pages.")
        pages += [p for p in range(a, b + 1) if p not in pages]
    return pages


def page_count(path: Path) -> int:
    try:
        with pymupdf.open(path) as doc:
            return doc.page_count
    except Exception as exc:
        raise ToolFailure("unreadable_document", f"Cannot open {path.name}: {exc}", hint="Send a PDF, PNG or JPEG.") from exc


def image_size(path: Path) -> tuple[int, int]:
    try:
        pix = pymupdf.Pixmap(str(path))
    except Exception as exc:
        raise ToolFailure("unreadable_image", f"Cannot decode {path.name}: {exc}", hint="Convert the image to PNG or JPEG.") from exc
    return pix.width, pix.height


def page_images(path: Path, pages: str | None = None, dpi: int = 150) -> list[tuple[int, bytes, str]]:
    """Return (page number, image bytes, mime) for each page. Indic-OCR takes one PNG or JPEG page per request."""
    if kind_of(path) == "image":
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"} and path.stat().st_size < 6_000_000:
            pix = pymupdf.Pixmap(str(path))
            if max(pix.width, pix.height) <= MAX_IMAGE_SIDE * 1.5:
                return [(1, path.read_bytes(), "image/jpeg" if path.suffix.lower() != ".png" else "image/png")]
        try:
            pix = pymupdf.Pixmap(str(path))
        except Exception as exc:
            raise ToolFailure("unreadable_image", f"Cannot decode {path.name}: {exc}", hint="Convert the image to PNG or JPEG.") from exc
        if pix.n - pix.alpha != 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        while max(pix.width, pix.height) > MAX_IMAGE_SIDE:
            pix.shrink(1)
        return [(1, pix.tobytes("png"), "image/png")]

    out = []
    with pymupdf.open(path) as doc:
        for no in parse_pages(pages, doc.page_count):
            rect = doc[no - 1].rect
            zoom = min(dpi / 72, MAX_IMAGE_SIDE / max(rect.width, rect.height))
            pix = doc[no - 1].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            out.append((no, pix.tobytes("png"), "image/png"))
    return out
