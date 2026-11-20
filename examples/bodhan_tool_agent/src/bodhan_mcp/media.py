"""Media plumbing in plain code: probing, decoding, silence-aware chunking, WAV joining, encoding,
PDF rasterisation and image preparation. No model calls happen here.

ffmpeg/ffprobe are used when present (any audio/video container). Without them, PCM WAV input and
WAV output still work, and every other format fails with an actionable ``ffmpeg_missing`` error.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .errors import FFMPEG_MISSING, MEDIA_DECODE_FAILED, UNSUPPORTED_FILE_TYPE, ToolFailure

AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wma", ".amr", ".weba"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".3gp", ".flv", ".wmv", ".mpeg", ".mpg", ".ts"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
PDF_EXT = {".pdf"}
TEXT_EXT = {".txt", ".md", ".markdown", ".srt", ".vtt", ".json", ".csv", ".tsv", ".rst", ".text", ".html", ".htm"}

ASR_SAMPLE_RATE = 16_000
MAX_ASR_SECONDS = 30.0  # hard limit of Indic-Transcribe per request
SAFE_ASR_SECONDS = 28.0  # headroom for container/rounding differences


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def ffprobe_path() -> str | None:
    return shutil.which("ffprobe")


def _sniff(path: Path) -> str:
    try:
        with path.open("rb") as fh:
            head = fh.read(64)
    except OSError:
        return "unknown"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG") or head[:3] == b"\xff\xd8\xff" or head[:4] in (b"GIF8", b"II*\x00", b"MM\x00*") or head[:2] == b"BM":
        return "image"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE" or head[:3] == b"ID3" or head[:4] in (b"fLaC", b"OggS") or head[:2] == b"\xff\xfb":
        return "audio"
    if head[4:8] == b"ftyp" or head[:4] == b"\x1aE\xdf\xa3":
        return "video"
    try:
        head.decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        return "unknown"


def file_kind(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in AUDIO_EXT:
        return "audio"
    if ext in VIDEO_EXT:
        return "video"
    if ext in IMAGE_EXT:
        return "image"
    if ext in PDF_EXT:
        return "pdf"
    if ext in TEXT_EXT:
        return "text"
    return _sniff(path)


def probe(path: Path) -> dict:
    """ffprobe summary (duration, streams). Falls back to the wave module for WAV files."""
    info: dict = {}
    if ffprobe_path():
        try:
            out = subprocess.run(
                [ffprobe_path(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                capture_output=True, timeout=30, check=False,
            )
            data = json.loads(out.stdout or b"{}")
            fmt = data.get("format", {})
            streams = data.get("streams", [])
            audio = [s for s in streams if s.get("codec_type") == "audio"]
            video = [s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1]
            info = {
                "duration_s": round(float(fmt.get("duration", 0) or 0), 2),
                "format": fmt.get("format_name"),
                "has_audio": bool(audio),
                "has_video": bool(video),
            }
            if audio:
                info["sample_rate"] = int(audio[0].get("sample_rate", 0) or 0)
                info["channels"] = audio[0].get("channels")
                info["audio_codec"] = audio[0].get("codec_name")
            if video:
                info["width"], info["height"] = video[0].get("width"), video[0].get("height")
            return info
        except (subprocess.SubprocessError, ValueError, OSError):
            info = {}
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as wf:
                return {
                    "duration_s": round(wf.getnframes() / float(wf.getframerate()), 2),
                    "format": "wav", "has_audio": True, "has_video": False,
                    "sample_rate": wf.getframerate(), "channels": wf.getnchannels(),
                }
        except (wave.Error, EOFError, OSError):
            pass
    return info


# ---------------------------------------------------------------------------
# decoding
# ---------------------------------------------------------------------------

def _resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst or len(x) == 0:
        return x
    n_out = int(round(len(x) * dst / src))
    t_out = np.linspace(0, len(x) - 1, n_out)
    return np.interp(t_out, np.arange(len(x)), x).astype(np.float32)


def _read_wav_stdlib(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wf:
        sw, ch, sr = wf.getsampwidth(), wf.getnchannels(), wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    if sw == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sw == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ToolFailure(MEDIA_DECODE_FAILED, f"Unsupported WAV sample width {sw * 8} bits.",
                          hint="Install ffmpeg to decode this file.")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


def load_audio(path: Path, sample_rate: int = ASR_SAMPLE_RATE) -> np.ndarray:
    """Decode any audio or video file to mono float32 PCM at ``sample_rate``."""
    ff = ffmpeg_path()
    if ff:
        proc = subprocess.run(
            [ff, "-nostdin", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-"],
            capture_output=True, timeout=600, check=False,
        )
        if proc.returncode != 0 or not proc.stdout:
            msg = proc.stderr.decode("utf-8", "replace").strip().splitlines()
            raise ToolFailure(MEDIA_DECODE_FAILED, f"Could not decode audio from {path.name}: {msg[-1] if msg else 'no audio stream'}",
                              hint="Check the file is a valid audio/video file with an audio track.")
        return np.frombuffer(proc.stdout, dtype="<i2").astype(np.float32) / 32768.0
    if path.suffix.lower() == ".wav":
        try:
            x, sr = _read_wav_stdlib(path)
        except (wave.Error, EOFError) as exc:
            raise ToolFailure(MEDIA_DECODE_FAILED, f"{path.name} is not a PCM WAV file ({exc}).",
                              hint="Install ffmpeg to decode compressed WAV variants.") from exc
        return _resample(x, sr, sample_rate)
    raise ToolFailure(FFMPEG_MISSING, f"ffmpeg is required to decode {path.suffix or 'this'} files.",
                      hint="Install ffmpeg (apt install ffmpeg / brew install ffmpeg / winget install ffmpeg), or pass a PCM WAV file.")


def to_wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    pcm = np.clip(samples, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


def wav_bytes_to_array(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data), "rb") as wf:
        sw, ch, sr = wf.getsampwidth(), wf.getnchannels(), wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    if sw != 2:
        raise ToolFailure(MEDIA_DECODE_FAILED, f"Expected 16-bit PCM WAV, got {sw * 8}-bit.")
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return x, sr


# ---------------------------------------------------------------------------
# silence-aware segmentation (energy-based voice activity detection)
# ---------------------------------------------------------------------------

@dataclass
class Segment:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def _frame_rms(x: np.ndarray, frame: int) -> np.ndarray:
    n = len(x) // frame
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    f = x[: n * frame].reshape(n, frame)
    return np.sqrt(np.mean(f * f, axis=1) + 1e-12)


def plan_segments(x: np.ndarray, sr: int, max_seconds: float = SAFE_ASR_SECONDS, min_seconds: float | None = None,
                  frame_ms: int = 30, pad_s: float = 0.15) -> list[Segment]:
    """Cut speech into segments no longer than ``max_seconds``, preferring cuts in pauses.

    Leading/trailing silence is trimmed (Bodhan bills per second of audio) and segments that
    contain no voiced frames are dropped.
    """
    max_seconds = min(max_seconds, SAFE_ASR_SECONDS)
    if min_seconds is None:
        min_seconds = max(1.0, max_seconds * 0.4)
    frame = max(1, int(sr * frame_ms / 1000))
    rms = _frame_rms(x, frame)
    if len(rms) == 0:
        return []
    fps = 1000.0 / frame_ms
    noise = float(np.percentile(rms, 10))
    peak = float(np.percentile(rms, 95))
    # In speech-dense audio the 10th percentile is itself speech, so cap the threshold relative to the peak.
    thr = max(min(noise * 3.0, peak * 0.25), peak * 0.05, 3e-4)
    voiced = rms > thr
    if not voiced.any():
        return []
    # Smooth so short dips inside words do not count as pauses.
    k = max(1, int(0.3 * fps))
    smooth = np.convolve(rms, np.ones(k) / k, mode="same")

    first = int(np.argmax(voiced))
    last = len(voiced) - int(np.argmax(voiced[::-1])) - 1
    pos, end = first, last + 1
    max_f, min_f = int(max_seconds * fps), int(min_seconds * fps)
    cuts: list[tuple[int, int]] = []
    while pos < end:
        if end - pos <= max_f:
            cuts.append((pos, end))
            break
        lo, hi = pos + min_f, min(pos + max_f, end)
        window = voiced[lo:hi]
        best, best_len, run_start = None, 0, None
        for i, v in enumerate(window):  # longest unvoiced run inside the allowed window
            if not v:
                run_start = i if run_start is None else run_start
                run_len = i - run_start + 1
                if run_len >= best_len:  # ">=" prefers later pauses, giving longer segments
                    best, best_len = lo + run_start + run_len // 2, run_len
            else:
                run_start = None
        if best is None:
            # No pause at all: cut at the quietest point in the last part of the window (fewer, longer requests).
            tail = lo + int(0.6 * (hi - lo))
            best = tail + int(np.argmin(smooth[tail:hi]))
        cuts.append((pos, best))
        pos = best
    segments = []
    for a, b in cuts:
        seg_voiced = voiced[a:b]
        if not seg_voiced.any():
            continue
        a2 = a + int(np.argmax(seg_voiced))
        b2 = b - int(np.argmax(seg_voiced[::-1]))
        # Pad into surrounding silence, but never past the cut points: no audio is sent twice.
        start = max(a / fps, a2 / fps - pad_s)
        stop = min(b / fps, b2 / fps + pad_s, len(x) / sr)
        segments.append(Segment(round(start, 3), round(min(stop, start + MAX_ASR_SECONDS - 0.5), 3)))
    return segments


def slice_wav(x: np.ndarray, sr: int, seg: Segment) -> bytes:
    return to_wav_bytes(x[int(seg.start * sr): int(seg.end * sr)], sr)


# ---------------------------------------------------------------------------
# joining and encoding
# ---------------------------------------------------------------------------

def concat_wavs(clips: list[bytes], gap_s: float = 0.25) -> bytes:
    if not clips:
        raise ToolFailure(MEDIA_DECODE_FAILED, "No audio clips to join.")
    arrays, rate = [], None
    for c in clips:
        x, sr = wav_bytes_to_array(c)
        rate = rate or sr
        arrays.append(_resample(x, sr, rate))
    gap = np.zeros(int(rate * gap_s), dtype=np.float32)
    parts = []
    for i, a in enumerate(arrays):
        if i:
            parts.append(gap)
        parts.append(a)
    return to_wav_bytes(np.concatenate(parts), rate)


def wav_duration(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate())


AI_LABEL = "AI-generated speech (Bodhan Indic-Speak). Not a human recording."


def write_audio(wav: bytes, out_path: Path, fmt: str, title: str = "AI-generated speech") -> tuple[Path, list[str]]:
    """Write ``wav`` as ``fmt`` (wav/mp3/ogg/flac). Returns the path written and any warnings."""
    warnings: list[str] = []
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "wav":
        out_path = out_path.with_suffix(".wav")
        out_path.write_bytes(wav)
        return out_path, warnings
    ff = ffmpeg_path()
    if not ff:
        out_path = out_path.with_suffix(".wav")
        out_path.write_bytes(wav)
        warnings.append(f"ffmpeg not found, so the audio was saved as WAV instead of {fmt}.")
        return out_path, warnings
    out_path = out_path.with_suffix("." + fmt)
    codec = {"mp3": ["-codec:a", "libmp3lame", "-q:a", "3"], "ogg": ["-codec:a", "libvorbis", "-q:a", "5"], "flac": ["-codec:a", "flac"]}[fmt]
    meta = ["-metadata", f"title={title}", "-metadata", f"comment={AI_LABEL}", "-metadata", "artist=Bodhan Indic-Speak (AI voice)"]
    proc = subprocess.run([ff, "-nostdin", "-v", "error", "-y", "-f", "wav", "-i", "-", *codec, *meta, str(out_path)],
                          input=wav, capture_output=True, timeout=300, check=False)
    if proc.returncode != 0:
        out_path = out_path.with_suffix(".wav")
        out_path.write_bytes(wav)
        warnings.append(f"ffmpeg could not encode {fmt} ({proc.stderr.decode('utf-8', 'replace')[-200:]}); saved WAV instead.")
    return out_path, warnings


# ---------------------------------------------------------------------------
# documents and images
# ---------------------------------------------------------------------------

MAX_IMAGE_SIDE = 2400
OCR_MIME = {"png": "image/png", "jpeg": "image/jpeg"}


def pdf_page_count(path: Path) -> int:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as exc:  # pypdfium2 raises PdfiumError, a plain Exception subclass
        raise ToolFailure(MEDIA_DECODE_FAILED, f"Could not open PDF {path.name}: {exc}",
                          hint="The file may be encrypted or corrupt.") from exc
    try:
        return len(pdf)
    finally:
        pdf.close()


def _encode_for_ocr(img) -> tuple[bytes, str]:
    from PIL import Image

    img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
    w, h = img.size
    scale = min(1.0, MAX_IMAGE_SIDE / max(w, h))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92, optimize=True)
    return buf.getvalue(), "image/jpeg"


def render_pdf_pages(path: Path, pages: list[int], dpi: int = 170) -> list[tuple[int, bytes, str]]:
    """Rasterise 1-based ``pages`` of a PDF to JPEG bytes for OCR (Indic-OCR does not accept PDFs)."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        out = []
        for p in pages:
            page = pdf[p - 1]
            pil = page.render(scale=dpi / 72).to_pil()
            data, mime = _encode_for_ocr(pil)
            out.append((p, data, mime))
            page.close()
        return out
    finally:
        pdf.close()


def image_frames(path: Path) -> int:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as im:
            return getattr(im, "n_frames", 1) if path.suffix.lower() in (".tif", ".tiff") else 1
    except (UnidentifiedImageError, OSError) as exc:
        raise ToolFailure(UNSUPPORTED_FILE_TYPE, f"{path.name} is not a readable image ({exc}).",
                          hint="Supported images: PNG, JPEG, WEBP, BMP, TIFF, GIF.") from exc


def prepare_image(path: Path, frame: int = 0) -> tuple[bytes, str, tuple[int, int]]:
    """Return OCR-ready bytes: EXIF-rotated, RGB, long side <= 2400 px. Small PNG/JPEG pass through unchanged."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(path) as im:
            if frame:
                im.seek(frame)
            fmt = (im.format or "").lower()
            exif_rot = im.getexif().get(0x0112, 1) if hasattr(im, "getexif") else 1
            size = im.size
            raw_ok = fmt in ("png", "jpeg") and frame == 0 and exif_rot in (0, 1) and max(size) <= MAX_IMAGE_SIDE \
                and path.stat().st_size <= 4_000_000
            if raw_ok:
                return path.read_bytes(), OCR_MIME[fmt], size
            img = ImageOps.exif_transpose(im)
            data, mime = _encode_for_ocr(img)
            return data, mime, img.size
    except (UnidentifiedImageError, OSError) as exc:
        raise ToolFailure(UNSUPPORTED_FILE_TYPE, f"{path.name} is not a readable image ({exc}).",
                          hint="Supported images: PNG, JPEG, WEBP, BMP, TIFF, GIF. Convert HEIC photos to JPEG first.") from exc
