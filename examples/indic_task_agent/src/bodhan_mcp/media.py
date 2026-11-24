"""Media helpers: inspect files, convert audio, cut it into windows at silences, render PDF pages, join WAVs.

Uses ffmpeg/ffprobe (system tools) and pypdfium2/Pillow (Python). No speech, OCR or translation happens here.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Any

from PIL import Image

AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".amr", ".webm"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
TEXT_EXT = {".txt", ".md", ".markdown"}
PDF_EXT = {".pdf"}

# Windows are cut at silences so no word is split. Indic-Transcribe accepts up to 30 seconds per request.
MAX_WINDOW_S = 28.0
SILENCE_NOISE = "-35dB"
SILENCE_MIN_S = 0.25


class MediaError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise MediaError("missing_dependency", f"{tool} is not installed. Install ffmpeg (for example: brew install ffmpeg).")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def probe(path: Path) -> dict[str, Any]:
    require_ffmpeg()
    proc = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    if proc.returncode != 0:
        raise MediaError("unreadable_media", f"Could not read {path.name}. Check that the file is not corrupt.")
    info = json.loads(proc.stdout or "{}")
    streams = info.get("streams", [])
    duration = float(info.get("format", {}).get("duration", 0) or 0)
    return {
        "duration_s": round(duration, 3),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "has_video": any(s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1 for s in streams),
    }


def inspect(path_str: str) -> dict[str, Any]:
    path = Path(path_str).expanduser()
    if not path.is_file():
        raise MediaError("file_not_found", f"No file at {path_str}. Check the path.")
    suffix = path.suffix.lower()
    size = path.stat().st_size
    base = {"path": str(path), "size_bytes": size, "suffix": suffix}
    if suffix in PDF_EXT:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        return {**base, "kind": "pdf", "pages": len(pdf)}
    if suffix in IMAGE_EXT:
        with Image.open(path) as img:
            return {**base, "kind": "image", "width": img.width, "height": img.height, "format": img.format}
    if suffix in TEXT_EXT:
        text = path.read_text(encoding="utf-8", errors="replace")
        return {**base, "kind": "text", "characters": len(text)}
    info = probe(path)
    if info["has_video"]:
        kind = "video"
    elif info["has_audio"]:
        kind = "audio"
    else:
        kind = "unknown"
    return {**base, "kind": kind, **info}


def to_wav16k(src: Path, dst: Path) -> None:
    """Mono 16 kHz PCM WAV, the format the transcription windows are cut from."""
    require_ffmpeg()
    dst.parent.mkdir(parents=True, exist_ok=True)
    proc = _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)])
    if proc.returncode != 0 or not dst.exists():
        raise MediaError("decode_failed", f"ffmpeg could not decode the audio: {proc.stderr.strip()[-200:]}")


def silence_midpoints(wav: Path) -> list[float]:
    require_ffmpeg()
    proc = _run(["ffmpeg", "-hide_banner", "-i", str(wav), "-af", f"silencedetect=noise={SILENCE_NOISE}:d={SILENCE_MIN_S}", "-f", "null", "-"])
    cuts = []
    for end, dur in re.findall(r"silence_end: ([\d.]+) \| silence_duration: ([\d.]+)", proc.stderr):
        cuts.append(float(end) - float(dur) / 2)
    return cuts


def plan_windows(duration: float, cuts: list[float], max_len: float = MAX_WINDOW_S) -> list[tuple[float, float]]:
    """Cut points are the middle of a silence. Each window is at most ``max_len`` seconds long."""
    windows: list[tuple[float, float]] = []
    start = 0.0
    cuts = sorted(cuts)
    while duration - start > max_len:
        limit = start + max_len
        preferred = [c for c in cuts if start + max_len * 0.5 < c <= limit]
        anything = [c for c in cuts if start + 1.0 < c <= limit]
        end = max(preferred) if preferred else (max(anything) if anything else limit)
        windows.append((start, end))
        start = end
    if duration - start > 0.05:
        windows.append((start, duration))
    return windows


def cut_wav(src: Path, start: float, end: float, dst: Path) -> None:
    require_ffmpeg()
    dst.parent.mkdir(parents=True, exist_ok=True)
    proc = _run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-c", "copy", str(dst)]
    )
    if proc.returncode != 0:
        raise MediaError("decode_failed", f"Could not cut audio at {start:.1f}s: {proc.stderr.strip()[-200:]}")


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def write_wav_bytes(data: bytes, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)


def concat_wavs(paths: list[Path], dst: Path, gap_s: float = 0.25) -> float:
    """Join WAVs that share one format, with a short pause between them. Returns the new duration."""
    if not paths:
        raise MediaError("empty_audio", "There was no audio to join.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(paths[0]), "rb") as first:
        params = first.getparams()
    gap_frames = int(params.framerate * gap_s)
    silence = b"\x00" * gap_frames * params.nchannels * params.sampwidth
    with wave.open(str(dst), "wb") as out:
        out.setparams(params)
        for i, p in enumerate(paths):
            with wave.open(str(p), "rb") as w:
                if w.getparams()[:3] != params[:3]:
                    raise MediaError("format_mismatch", "Speech chunks have different WAV formats.")
                out.writeframes(w.readframes(w.getnframes()))
            if i < len(paths) - 1:
                out.writeframes(silence)
    return wav_duration(dst)


def render_pdf_pages(pdf: Path, out_dir: Path, scale: float = 2.5) -> list[Path]:
    """Each PDF page as a PNG, at about 180 dpi."""
    import pypdfium2 as pdfium

    out_dir.mkdir(parents=True, exist_ok=True)
    document = pdfium.PdfDocument(str(pdf))
    paths = []
    for i in range(len(document)):
        page = document[i]
        image = page.render(scale=scale).to_pil()
        target = out_dir / f"page-{i + 1:03d}.png"
        image.save(target, "PNG")
        paths.append(target)
    return paths


def image_for_ocr(path: Path, out_dir: Path) -> tuple[bytes, str]:
    """PNG or JPEG bytes for OCR. Other formats are converted to PNG."""
    if path.suffix.lower() in {".png"}:
        return path.read_bytes(), "image/png"
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        return path.read_bytes(), "image/jpeg"
    with Image.open(path) as img:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "PNG")
    return buf.getvalue(), "image/png"


def wav_to_mp3(src: Path, dst: Path) -> None:
    require_ffmpeg()
    proc = _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-codec:a", "libmp3lame", "-q:a", "3", str(dst)])
    if proc.returncode != 0:
        raise MediaError("encode_failed", f"Could not write MP3: {proc.stderr.strip()[-200:]}")
