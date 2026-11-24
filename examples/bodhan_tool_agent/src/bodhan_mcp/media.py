"""Media plumbing: probing, audio windows, PDF pages, text chunking.

Deterministic code only — no model calls here, so everything is unit-testable.
"""

from __future__ import annotations

import io
import json
import mimetypes
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import config
from .errors import FILE_NOT_FOUND, INVALID_INPUT, UNSUPPORTED_MEDIA, ToolError

AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".webm", ".amr", ".wma"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
TEXT_EXT = {".txt", ".md", ".markdown", ".srt", ".vtt", ".csv", ".json"}
SAMPLE_RATE = 16000


def require_file(path: str | Path) -> Path:
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        raise ToolError(FILE_NOT_FOUND, f"File not found: {path}", hint="Pass a path returned by inspect_media or a previous tool.")
    if p.stat().st_size == 0:
        raise ToolError(INVALID_INPUT, f"File is empty: {path}")
    return p


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    if not ffmpeg_available():
        raise ToolError(UNSUPPORTED_MEDIA, "ffmpeg/ffprobe not installed.", hint="Install ffmpeg (see README).")
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise ToolError(UNSUPPORTED_MEDIA, f"Could not decode media: {proc.stderr.decode(errors='ignore')[-300:]}")
    return proc


def kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in IMAGE_EXT:
        return "image"
    if ext in TEXT_EXT:
        return "text"
    if ext in AUDIO_EXT - {".webm"}:
        return "audio"
    if ext in VIDEO_EXT:
        return "video"
    mime = mimetypes.guess_type(path.name)[0] or ""
    return mime.split("/")[0] if mime else "unknown"


def probe(path: Path) -> dict:
    proc = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                 "-of", "json", str(path)], timeout=60)
    info = json.loads(proc.stdout or b"{}")
    streams = {s.get("codec_type") for s in info.get("streams", [])}
    return {"duration_sec": round(float(info.get("format", {}).get("duration", 0) or 0), 2),
            "has_audio": "audio" in streams, "has_video": "video" in streams}


# ---------------------------------------------------------------------- audio
def decode_to_pcm(path: Path) -> np.ndarray:
    """Any audio/video -> mono 16 kHz int16 samples."""
    proc = _run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
                 "-f", "s16le", "-"])
    pcm = np.frombuffer(proc.stdout, dtype=np.int16)
    if pcm.size == 0:
        raise ToolError(UNSUPPORTED_MEDIA, f"No audio stream found in {path.name}.")
    return pcm


def pcm_to_wav(pcm: np.ndarray, rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm.astype(np.int16).tobytes())
    return buf.getvalue()


@dataclass
class Window:
    index: int
    start: float
    end: float
    pcm: np.ndarray

    @property
    def duration(self) -> float:
        return self.end - self.start


def frame_energy(pcm: np.ndarray, frame_ms: int = 50) -> np.ndarray:
    n = SAMPLE_RATE * frame_ms // 1000
    usable = pcm[: len(pcm) // n * n].astype(np.float32)
    if usable.size == 0:
        return np.zeros(0)
    return np.sqrt((usable.reshape(-1, n) ** 2).mean(axis=1))


def split_audio(pcm: np.ndarray, max_sec: float = config.ASR_WINDOW_SEC, min_sec: float = config.ASR_MIN_WINDOW_SEC,
                frame_ms: int = 50) -> list[Window]:
    """Cut into <= max_sec windows at the quietest point after min_sec, so words are not split.

    Windows that are almost entirely silent are dropped (never "transcribe" silence).
    """
    energy = frame_energy(pcm, frame_ms)
    fps = 1000 // frame_ms
    total_frames = int(np.ceil(len(pcm) / (SAMPLE_RATE * frame_ms / 1000)))
    speech_level = max(150.0, 0.05 * float(np.percentile(energy, 95))) if energy.size else 150.0
    windows: list[Window] = []
    start = 0
    max_f, min_f = int(max_sec * fps), int(min_sec * fps)
    while start < total_frames:
        if total_frames - start <= max_f:
            end = total_frames
        else:
            lo, hi = start + min_f, min(start + max_f, len(energy))
            seg = energy[lo:hi]
            end = lo + int(np.argmin(seg)) if seg.size else start + max_f
        s0, s1 = start * SAMPLE_RATE * frame_ms // 1000, min(len(pcm), end * SAMPLE_RATE * frame_ms // 1000)
        chunk = pcm[s0:s1]
        e = energy[start:end]
        speechy = (e > speech_level).mean() if e.size else 0
        if chunk.size and speechy > 0.03:
            windows.append(Window(len(windows), round(s0 / SAMPLE_RATE, 2), round(s1 / SAMPLE_RATE, 2), chunk))
        start = end
    return windows


def concat_wavs(blobs: list[bytes], gap_ms: int = 120) -> tuple[bytes, float]:
    """Join TTS clips (same format) with a short pause; returns (wav bytes, duration)."""
    params, frames = None, []
    for b in blobs:
        with wave.open(io.BytesIO(b), "rb") as wf:
            if params is None:
                params = wf.getparams()
            elif (wf.getframerate(), wf.getsampwidth(), wf.getnchannels()) != (params.framerate, params.sampwidth, params.nchannels):
                raise ToolError(UNSUPPORTED_MEDIA, "TTS clips came back in different formats.")
            frames.append(wf.readframes(wf.getnframes()))
    if params is None:
        raise ToolError(INVALID_INPUT, "Nothing to join.")
    silence = b"\x00" * int(params.framerate * gap_ms / 1000) * params.sampwidth * params.nchannels
    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setparams(params)
        wf.writeframes(silence.join(frames))
    n = sum(len(f) for f in frames) + len(silence) * (len(frames) - 1)
    return out.getvalue(), round(n / (params.framerate * params.sampwidth * params.nchannels), 2)


def write_audio(wav: bytes, out: Path, fmt: str, title: str) -> Path:
    """Save as wav or mp3 (via ffmpeg), tagged as AI-generated."""
    out.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "wav":
        out = out.with_suffix(".wav")
        out.write_bytes(wav)
        return out
    out = out.with_suffix(".mp3")
    proc = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "wav", "-i", "-", "-codec:a", "libmp3lame",
                           "-q:a", "4", "-metadata", f"title={title}", "-metadata", "comment=AI-generated speech (Bodhan Indic-Speak)",
                           str(out)], input=wav, capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise ToolError(UNSUPPORTED_MEDIA, f"mp3 encode failed: {proc.stderr.decode(errors='ignore')[-200:]}")
    return out


# ---------------------------------------------------------------------- images / pdf
def pdf_pages(path: Path, dpi: int = config.OCR_PDF_DPI, max_pages: int = config.OCR_MAX_PAGES) -> list[bytes]:
    """Render each page to PNG (never uses the PDF text layer)."""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as exc:  # noqa: BLE001
        raise ToolError(UNSUPPORTED_MEDIA, f"Cannot open PDF {path.name}: {exc}") from exc
    if len(pdf) > max_pages:
        raise ToolError(INVALID_INPUT, f"PDF has {len(pdf)} pages; limit is {max_pages}.", hint="Pass a page range.")
    out = []
    for i in range(len(pdf)):
        img = pdf[i].render(scale=dpi / 72).to_pil()
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        out.append(buf.getvalue())
    return out


def pdf_page_count(path: Path) -> int:
    import pypdfium2 as pdfium

    return len(pdfium.PdfDocument(str(path)))


def image_for_ocr(path: Path, max_side: int = 2400) -> tuple[bytes, str]:
    """Normalise any image to PNG/JPEG within a sane size (EXIF-rotated)."""
    from PIL import Image, ImageOps

    try:
        img = ImageOps.exif_transpose(Image.open(path))
    except Exception as exc:  # noqa: BLE001
        raise ToolError(UNSUPPORTED_MEDIA, f"Cannot read image {path.name}: {exc}") from exc
    img = img.convert("RGB")
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue(), "image/jpeg"


# ---------------------------------------------------------------------- text
SENT_END = re.compile(r"(?<=[।॥.!?;:])\s+|\n+")


def split_sentences(text: str, max_chars: int = config.TTS_MAX_CHARS) -> list[str]:
    """Sentence pieces <= max_chars; long sentences are split at commas, then at spaces."""
    pieces: list[str] = []
    for sent in (s.strip() for s in SENT_END.split(text) if s and s.strip()):
        while len(sent) > max_chars:
            cut = max(sent.rfind(",", 0, max_chars), sent.rfind("،", 0, max_chars))
            if cut < max_chars // 3:
                cut = sent.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            pieces.append(sent[: cut + 1].strip())
            sent = sent[cut + 1:].strip()
        if sent:
            pieces.append(sent)
    return [p for p in pieces if re.search(r"\w", p)]


def split_blocks(text: str, max_chars: int = config.TRANSLATE_MAX_CHARS) -> list[str]:
    """Paragraph-aware chunks for translation. Markdown tables and code fences are never split."""
    blocks, cur, in_fence = [], [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
        cur.append(line)
        if not in_fence and not line.strip():
            blocks.append("\n".join(cur).strip("\n"))
            cur = []
    if cur:
        blocks.append("\n".join(cur).strip("\n"))
    blocks = [b for b in blocks if b.strip()]
    chunks, buf = [], ""
    for b in blocks:
        if len(b) > max_chars and not b.lstrip().startswith("|"):
            for s in split_sentences(b, max_chars):
                if len(buf) + len(s) + 1 > max_chars and buf:
                    chunks.append(buf)
                    buf = ""
                buf = f"{buf} {s}".strip()
            continue
        if len(buf) + len(b) + 2 > max_chars and buf:
            chunks.append(buf)
            buf = ""
        buf = f"{buf}\n\n{b}" if buf else b
    if buf:
        chunks.append(buf)
    return chunks
