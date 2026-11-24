"""Media plumbing: file typing, ffmpeg probing and decoding, voice-activity
segmentation, WAV helpers, audio export, PDF rendering and image preparation.

Everything here is synchronous and CPU or subprocess bound; tools run it in a
worker thread so the event loop stays responsive.
"""

from __future__ import annotations

import io
import logging
import mimetypes
import re
import shutil
import subprocess
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

ASR_SAMPLE_RATE = 16_000

AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wma", ".amr", ".aiff", ".aif"}
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".3gp", ".wmv", ".flv", ".ts", ".mpeg", ".mpg"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".heic", ".heif"}
TEXT_EXT = {".txt", ".md", ".markdown", ".json", ".srt", ".vtt", ".csv", ".tsv", ".html", ".htm", ".rst",
            ".log", ".text", ".xml", ".yaml", ".yml"}


class MediaError(Exception):
    """Raised for unreadable or undecodable media; tools turn it into a ToolFailure."""


# ---------------------------------------------------------------------------
# File typing
# ---------------------------------------------------------------------------

def _sniff(head: bytes) -> str | None:
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG") or head.startswith(b"\xff\xd8\xff") or head[:4] in (b"GIF8",) \
            or (head.startswith(b"RIFF") and head[8:12] == b"WEBP"):
        return "image"
    if (head.startswith(b"RIFF") and head[8:12] == b"WAVE") or head.startswith((b"ID3", b"fLaC", b"OggS")) \
            or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio"
    if head[4:8] == b"ftyp":
        return "video"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video"
    try:
        head.decode("utf-8")
        return "text" if head else None
    except UnicodeDecodeError:
        # A multibyte char may be cut at the boundary; retry without the tail.
        try:
            head[:-4].decode("utf-8")
            return "text"
        except UnicodeDecodeError:
            return None


def classify(path: Path) -> tuple[str, str]:
    """Return (kind, mime) where kind is audio|video|image|pdf|text|unknown."""
    ext = path.suffix.lower()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if ext == ".pdf":
        return "pdf", "application/pdf"
    if ext in AUDIO_EXT:
        return "audio", mime if mime.startswith("audio") else f"audio/{ext[1:]}"
    if ext in VIDEO_EXT:
        return "video", mime if mime.startswith("video") else f"video/{ext[1:]}"
    if ext in IMAGE_EXT:
        return "image", mime if mime.startswith("image") else f"image/{ext[1:]}"
    if ext in TEXT_EXT:
        return "text", mime if mime != "application/octet-stream" else "text/plain"
    try:
        with path.open("rb") as fh:
            head = fh.read(512)
    except OSError:
        return "unknown", mime
    kind = _sniff(head) or "unknown"
    if kind == "text":
        mime = "text/plain"
    return kind, mime


# ---------------------------------------------------------------------------
# ffmpeg
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001 - any failure here means "no ffmpeg"
        raise MediaError("ffmpeg is not installed and the bundled imageio-ffmpeg binary is unavailable") from exc


@dataclass
class MediaInfo:
    duration_s: float | None
    has_audio: bool
    has_video: bool
    width: int | None = None
    height: int | None = None
    audio_codec: str | None = None


_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_STREAM = re.compile(r"Stream #\S+.*?:\s*(Audio|Video):\s*([^\s,]+)(.*)")
_SIZE = re.compile(r",\s*(\d{2,5})x(\d{2,5})")


def probe(path: Path) -> MediaInfo:
    """Inspect a media file with `ffmpeg -i` (ffprobe is not always available)."""
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-nostdin", "-i", str(path)],
        capture_output=True, timeout=60,
    )
    err = proc.stderr.decode("utf-8", "replace")
    if "Invalid data found" in err or "could not find codec" in err.lower() or "No such file" in err:
        raise MediaError(err.strip().splitlines()[-1] if err.strip() else "ffmpeg could not read the file")
    duration = None
    m = _DURATION.search(err)
    if m:
        h, mi, s = m.groups()
        duration = int(h) * 3600 + int(mi) * 60 + float(s)
    info = MediaInfo(duration_s=duration, has_audio=False, has_video=False)
    for kind, codec, rest in _STREAM.findall(err):
        if kind == "Audio":
            info.has_audio = True
            info.audio_codec = info.audio_codec or codec
        elif "attached pic" not in rest:  # cover art in an MP3 is not video
            info.has_video = True
            size = _SIZE.search(rest)
            if size and info.width is None:
                info.width, info.height = int(size.group(1)), int(size.group(2))
    if not info.has_audio and not info.has_video:
        raise MediaError("no audio or video streams found")
    return info


def decode_audio(path: Path, sample_rate: int = ASR_SAMPLE_RATE) -> np.ndarray:
    """Decode any audio/video file to mono PCM16 at `sample_rate`."""
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path),
         "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-acodec", "pcm_s16le", "-"],
        capture_output=True, timeout=1800,
    )
    if proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise MediaError(msg[-1] if msg else "ffmpeg failed to decode the audio")
    return np.frombuffer(proc.stdout, dtype=np.int16).copy()


# ---------------------------------------------------------------------------
# Voice activity detection
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Window:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


FRAME_S = 0.03


def frame_db(pcm: np.ndarray, sr: int, frame_s: float = FRAME_S) -> np.ndarray:
    n = max(1, int(sr * frame_s))
    count = len(pcm) // n
    if count == 0:
        return np.array([], dtype=np.float32)
    frames = pcm[: count * n].astype(np.float32).reshape(count, n) / 32768.0
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    return (20.0 * np.log10(rms + 1e-10)).astype(np.float32)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index pairs of True runs."""
    if mask.size == 0:
        return []
    padded = np.concatenate([[False], mask, [False]])
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return list(zip(edges[0::2].tolist(), edges[1::2].tolist(), strict=True))


def speech_mask(db: np.ndarray, min_gap_s: float = 0.3, min_speech_s: float = 0.12) -> np.ndarray:
    if db.size == 0:
        return np.zeros(0, dtype=bool)
    floor = float(np.percentile(db, 10))
    peak = float(np.percentile(db, 99))
    if peak < -55.0:
        return np.zeros(db.size, dtype=bool)
    # Adaptive threshold between noise floor and loud speech. When there is little
    # dynamic range (wall-to-wall speech) anything above near-silence counts.
    thr = -55.0 if peak - floor < 12 else max(floor + 0.3 * (peak - floor), -55.0)
    mask = db > thr
    gap = int(round(min_gap_s / FRAME_S))
    for s, e in _runs(~mask):
        if 0 < s and e < mask.size and e - s <= gap:
            mask[s:e] = True
    shortest = int(round(min_speech_s / FRAME_S))
    for s, e in _runs(mask):
        if e - s < shortest:
            mask[s:e] = False
    return mask


def _split_long(start: int, end: int, db: np.ndarray, max_frames: int, min_frames: int) -> list[tuple[int, int]]:
    """Cut a continuous speech run at its quietest points so no piece exceeds max_frames."""
    pieces = []
    smooth = np.convolve(db, np.ones(5) / 5, mode="same") if db.size >= 5 else db
    while end - start > max_frames:
        lo, hi = start + min_frames, start + max_frames
        # Among equally quiet frames take the latest, so windows stay long and calls few.
        span = smooth[lo:hi][::-1]
        cut = hi - 1 - int(np.argmin(span))
        pieces.append((start, cut))
        start = cut
    pieces.append((start, end))
    return pieces


def vad_windows(
    pcm: np.ndarray,
    sr: int = ASR_SAMPLE_RATE,
    max_window_s: float = 28.0,
    max_gap_s: float = 1.5,
    pad_s: float = 0.15,
) -> list[Window]:
    """Group speech into windows of at most `max_window_s`, cut at pauses.

    Pure silence is never part of any window. Pauses longer than `max_gap_s`
    end a window, so long silences are not billed. A stretch of speech with no
    pause long enough is cut at its quietest frame, which is almost always
    between words.
    """
    db = frame_db(pcm, sr)
    mask = speech_mask(db)
    max_frames = int(max_window_s / FRAME_S) - int(2 * pad_s / FRAME_S) - 1
    min_frames = max(1, int(max_frames * 0.5))
    regions: list[tuple[int, int]] = []
    for s, e in _runs(mask):
        regions.extend(_split_long(s, e, db, max_frames, min_frames))

    total = len(pcm) / sr
    windows: list[Window] = []
    cur: list[float] | None = None
    for s, e in regions:
        start = max(0.0, s * FRAME_S - pad_s)
        end = min(total, e * FRAME_S + pad_s)
        if cur is not None and start - cur[1] <= max_gap_s and end - cur[0] <= max_window_s:
            cur[1] = end
            continue
        if cur is not None:
            windows.append(Window(round(cur[0], 3), round(cur[1], 3)))
            start = max(start, cur[1])
        cur = [start, end]
    if cur is not None:
        windows.append(Window(round(cur[0], 3), round(cur[1], 3)))
    return windows


# ---------------------------------------------------------------------------
# WAV helpers
# ---------------------------------------------------------------------------

def pcm_to_wav(pcm: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.asarray(pcm, dtype=np.int16).tobytes())
    return buf.getvalue()


def slice_wav(pcm: np.ndarray, sr: int, window: Window) -> bytes:
    return pcm_to_wav(pcm[int(window.start * sr): int(window.end * sr)], sr)


def wav_to_pcm(data: bytes) -> tuple[np.ndarray, int]:
    """Decode WAV bytes (PCM8/16/32, any channel count) to mono int16."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
            raw = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as exc:
        raise MediaError(f"invalid WAV data: {exc}") from exc
    if width == 2:
        pcm = np.frombuffer(raw, dtype=np.int16)
    elif width == 1:
        pcm = ((np.frombuffer(raw, dtype=np.uint8).astype(np.int16) - 128) << 8).astype(np.int16)
    elif width == 4:
        pcm = (np.frombuffer(raw, dtype=np.int32) >> 16).astype(np.int16)
    else:
        raise MediaError(f"unsupported WAV sample width {width}")
    if ch > 1:
        pcm = pcm[: len(pcm) // ch * ch].reshape(-1, ch).mean(axis=1).astype(np.int16)
    return pcm.copy(), sr


def decode_audio_bytes(data: bytes, sample_rate: int = 24_000) -> tuple[np.ndarray, int]:
    """Decode encoded audio (WAV fast path, anything else through ffmpeg) to mono int16."""
    if data[:4] == b"RIFF":
        try:
            return wav_to_pcm(data)
        except MediaError:
            pass  # e.g. a float or extensible WAV; ffmpeg handles those
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", "pipe:0",
         "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-acodec", "pcm_s16le", "-"],
        input=data, capture_output=True, timeout=300,
    )
    if proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise MediaError(msg[-1] if msg else "ffmpeg failed to decode audio bytes")
    return np.frombuffer(proc.stdout, dtype=np.int16).copy(), sample_rate


def resample(pcm: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    if sr_from == sr_to or len(pcm) == 0:
        return pcm
    n_out = int(round(len(pcm) * sr_to / sr_from))
    x_old = np.linspace(0.0, 1.0, num=len(pcm), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, pcm.astype(np.float32)).astype(np.int16)


def trim_silence(pcm: np.ndarray, sr: int, threshold_db: float = -45.0, keep_s: float = 0.05) -> np.ndarray:
    db = frame_db(pcm, sr, 0.01)
    loud = np.flatnonzero(db > threshold_db)
    if loud.size == 0:
        return pcm[:0]
    n = int(sr * 0.01)
    keep = int(sr * keep_s)
    start = max(0, loud[0] * n - keep)
    end = min(len(pcm), (loud[-1] + 1) * n + keep)
    return pcm[start:end]


def concat_with_pauses(clips: list[np.ndarray], sr: int, pauses_s: list[float]) -> np.ndarray:
    """Join clips; pauses_s[i] is the silence inserted after clips[i] (the last is ignored)."""
    parts: list[np.ndarray] = []
    for i, clip in enumerate(clips):
        parts.append(clip.astype(np.int16))
        if i < len(clips) - 1:
            parts.append(np.zeros(int(sr * pauses_s[i]), dtype=np.int16))
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16)


def export_audio(pcm: np.ndarray, sr: int, out_path: Path, metadata: dict[str, str]) -> Path:
    """Write mono PCM as MP3 or WAV (by suffix) with metadata tags. Falls back to WAV if MP3 encoding fails."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    args = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "s16le", "-ar", str(sr), "-ac", "1", "-i", "-"]
    for k, v in metadata.items():
        args += ["-metadata", f"{k}={v}"]
    if out_path.suffix.lower() == ".mp3":
        args += ["-codec:a", "libmp3lame", "-q:a", "3", "-id3v2_version", "3"]
    args.append(str(out_path))
    try:
        proc = subprocess.run(args, input=np.asarray(pcm, dtype=np.int16).tobytes(), capture_output=True,
                              timeout=600)
        if proc.returncode == 0 and out_path.exists():
            return out_path
        log.warning("ffmpeg export failed: %s", proc.stderr.decode("utf-8", "replace")[-300:])
    except (OSError, subprocess.SubprocessError, MediaError) as exc:
        log.warning("ffmpeg export failed: %s", exc)
    wav_path = out_path.with_suffix(".wav")
    wav_path.write_bytes(pcm_to_wav(pcm, sr))
    return wav_path


# ---------------------------------------------------------------------------
# Documents and images
# ---------------------------------------------------------------------------

OCR_TARGET_LONG_SIDE = 2200
OCR_MAX_LONG_SIDE = 3500
OCR_MAX_BYTES = 8 * 1024 * 1024


def pdf_page_count(path: Path) -> int:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as exc:
        raise MediaError(f"cannot open PDF: {exc}") from exc
    try:
        return len(pdf)
    finally:
        pdf.close()


def render_pdf_page(path: Path, index: int) -> tuple[bytes, int, int]:
    """Render one 0-based page to PNG sized for OCR. Returns (png, width, height)."""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as exc:
        raise MediaError(f"cannot open PDF: {exc}") from exc
    try:
        page = pdf[index]
        w, h = page.get_size()
        scale = min(4.0, OCR_TARGET_LONG_SIDE / max(w, h, 1.0))
        image = page.render(scale=scale).to_pil().convert("RGB")
        page.close()
    finally:
        pdf.close()
    return _encode_png_or_jpeg(image)


def _encode_png_or_jpeg(image) -> tuple[bytes, int, int]:
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=False)
    data = buf.getvalue()
    if len(data) > OCR_MAX_BYTES:
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=92)
        data = buf.getvalue()
    return data, image.width, image.height


def image_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    _register_heif()
    try:
        with Image.open(path) as im:
            return im.width, im.height
    except Exception as exc:  # noqa: BLE001 - Pillow raises many types for bad files
        raise MediaError(f"cannot read image: {exc}") from exc


def _register_heif() -> bool:
    try:
        from pillow_heif import register_heif_opener  # optional dependency
    except ImportError:
        return False
    register_heif_opener()
    return True


def prepare_image(path: Path) -> tuple[bytes, str, int, int]:
    """Load an image for OCR: fix EXIF rotation, flatten to RGB, bound the size.

    The API takes PNG or JPEG only. Small, upright PNG/JPEG files are sent as-is.
    Returns (bytes, mime, width, height).
    """
    from PIL import Image, ImageOps

    if path.suffix.lower() in {".heic", ".heif"} and not _register_heif():
        raise MediaError("HEIC/HEIF images need the optional pillow-heif package; convert to JPEG or PNG")
    try:
        im = Image.open(path)
        im.load()
    except Exception as exc:  # noqa: BLE001
        raise MediaError(f"cannot read image: {exc}") from exc
    fmt = (im.format or "").upper()
    orientation = im.getexif().get(0x0112, 1) if hasattr(im, "getexif") else 1
    raw = path.read_bytes()
    if fmt in {"PNG", "JPEG"} and orientation in (1, None) and max(im.size) <= OCR_MAX_LONG_SIDE \
            and len(raw) <= OCR_MAX_BYTES and im.mode in {"RGB", "L", "RGBA", "P"}:
        return raw, "image/png" if fmt == "PNG" else "image/jpeg", im.width, im.height
    if getattr(im, "n_frames", 1) > 1:
        im.seek(0)
    im = ImageOps.exif_transpose(im)
    if im.mode in ("RGBA", "LA", "P"):
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    if max(im.size) > OCR_MAX_LONG_SIDE:
        ratio = OCR_MAX_LONG_SIDE / max(im.size)
        im = im.resize((max(1, int(im.width * ratio)), max(1, int(im.height * ratio))), Image.LANCZOS)
    data, w, h = _encode_png_or_jpeg(im)
    return data, ("image/png" if data[:4] == b"\x89PNG" else "image/jpeg"), w, h
