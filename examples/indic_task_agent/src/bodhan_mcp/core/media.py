"""Local media helpers over ffmpeg/ffprobe/PyMuPDF/Pillow. No network."""
from __future__ import annotations

import io
import json
import re
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

from .config import ASR_MAX_WINDOW_S, ASR_TARGET_WINDOW_S, OCR_MAX_EDGE_PX
from .errors import BodhanError

_DECODABLE = "Supported: wav, flac, ogg, mp3, m4a, aac, webm, amr, mp4, mov, mkv."
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}
_SILENCE_RE = re.compile(r"silence_(start|end):\s*(-?\d+(?:\.\d+)?)")
_PEAK_RE = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?) dB")


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise BodhanError("INTERNAL", f"{cmd[0]} is not installed.", hint="Install ffmpeg.") from exc


def _existing(path: str | Path) -> Path:
    p = Path(path)
    if not p.is_file():
        raise BodhanError("MISSING_FILE", f"{p} does not exist.", hint="Pass a readable file path.")
    return p


def _cannot_decode(p: Path, detail: str) -> BodhanError:
    return BodhanError(
        "UNSUPPORTED_FORMAT",
        f"ffmpeg could not decode {p.name}.",
        hint=f"{_DECODABLE} {detail}".strip(),
    )


def _float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def ffprobe_json(path: str | Path) -> dict:
    """ffprobe `-show_format -show_streams` output as a dict."""
    p = _existing(path)
    proc = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(p)])
    if proc.returncode != 0:
        raise _cannot_decode(p, proc.stderr.strip()[:200])
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise _cannot_decode(p, "unreadable probe output") from exc


def probe_audio(path: str | Path) -> dict:
    """Duration, sample rate, channels, codec and whether an audio track exists."""
    info = ffprobe_json(path)
    streams = info.get("streams") or []
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = _float((info.get("format") or {}).get("duration"))
    if audio is None:
        return {"duration_s": duration, "sample_rate": None, "channels": None, "codec": None, "has_audio": False}
    return {
        "duration_s": duration or _float(audio.get("duration")) or 0.0,
        "sample_rate": int(audio.get("sample_rate") or 0) or None,
        "channels": int(audio["channels"]) if audio.get("channels") else None,
        "codec": audio.get("codec_name"),
        "has_audio": True,
    }


def probe_video(path: str | Path) -> dict:
    """Duration, frame size, fps and audio-track presence for a video container."""
    info = ffprobe_json(path)
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return {
        "duration_s": _float((info.get("format") or {}).get("duration")),
        "width": int(video["width"]) if video and video.get("width") else None,
        "height": int(video["height"]) if video and video.get("height") else None,
        "fps": _fraction(video.get("r_frame_rate")) if video else None,
        "codec": video.get("codec_name") if video else None,
        "has_audio": audio is not None,
        "sample_rate": int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        "channels": int(audio["channels"]) if audio and audio.get("channels") else None,
    }


def _fraction(value: object) -> float | None:
    try:
        num, _, den = str(value).partition("/")
        return float(num) / float(den or 1)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def is_silent(path: str | Path, threshold_db: float = -60.0, max_s: float | None = None) -> bool:
    """True when the loudest sample is below `threshold_db`. False if unmeasurable."""
    peak = _peak_db(_existing(path), max_s)
    return peak is not None and peak <= threshold_db


def to_wav(path: str | Path, out_path: str | Path) -> Path:
    """Decode any supported container to 16 kHz mono PCM16 WAV."""
    p = _existing(path)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = _run(
        ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-v", "error", "-i", str(p),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)]
    )
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size <= 44:
        raise _cannot_decode(p, proc.stderr.strip()[:200])
    return out


def _peak_db(path: Path, max_s: float | None = None) -> float | None:
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path)]
    if max_s:
        cmd += ["-t", f"{max_s:.1f}"]  # a long video is sampled, not decoded end to end
    cmd += ["-af", "volumedetect", "-f", "null", "-"]
    proc = _run(cmd)
    match = _PEAK_RE.search(proc.stderr or "")
    return float(match.group(1)) if match else None


def _silences(path: Path, threshold_db: float, min_s: float = 0.25) -> list[tuple[float, float]]:
    proc = _run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-i", str(path),
         "-af", f"silencedetect=noise={threshold_db:.1f}dB:d={min_s}", "-f", "null", "-"]
    )
    if proc.returncode != 0:
        raise _cannot_decode(path, proc.stderr.strip()[:200])
    found: list[tuple[float, float]] = []
    start: float | None = None
    for kind, value in _SILENCE_RE.findall(proc.stderr or ""):
        if kind == "start":
            start = float(value)
        elif start is not None:
            found.append((start, float(value)))
            start = None
    return found


def silence_cut_points(
    wav_path: str | Path,
    target_s: float = ASR_TARGET_WINDOW_S,
    cap_s: float = ASR_MAX_WINDOW_S,
) -> list[tuple[float, float, str]]:
    """Split a WAV at natural pauses into windows of at most `cap_s`.

    Each window is `(start_s, end_s, cut)` where `cut` is `"pause"` when the window
    ended on a detected silence midpoint and `"hard"` when it was cut at `cap_s`.
    Silences are >=250 ms at 25 dB below the file's peak. A pause-free file still
    yields full-length windows, every one marked `"hard"`.
    """
    path = Path(wav_path)
    duration = wav_duration(path)
    if duration <= 0:
        return []
    peak = _peak_db(path)
    threshold = max(-90.0, (peak if peak is not None else 0.0) - 25.0)
    midpoints = [(a + b) / 2.0 for a, b in _silences(path, threshold)]

    windows: list[tuple[float, float, str]] = []
    start = 0.0
    while start < duration - 1e-3:
        limit = min(start + cap_s, duration)
        target = start + target_s
        candidates = [m for m in midpoints if start + 1.0 < m <= limit]
        if limit >= duration:  # don't leave an unusably short tail
            candidates = [m for m in candidates if duration - m >= 1.0]
        if candidates:
            end = min(candidates, key=lambda m: abs(m - target))
            windows.append((start, end, "pause"))
        else:
            end = limit
            windows.append((start, end, "hard"))
        start = end
    return windows


def wav_duration(path: str | Path) -> float:
    return float(probe_audio(path)["duration_s"] or 0.0)


def wav_slice(wav_path: str | Path, start_s: float, end_s: float) -> bytes:
    """A self-contained WAV blob for `[start_s, end_s)`."""
    p = _existing(wav_path)
    duration = max(0.0, float(end_s) - float(start_s))
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "slice.wav"
        proc = _run(
            ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-v", "error", "-ss", f"{float(start_s):.3f}",
             "-i", str(p), "-t", f"{duration:.3f}", "-c:a", "pcm_s16le", str(out)]
        )
        if proc.returncode != 0 or not out.is_file():
            raise _cannot_decode(p, proc.stderr.strip()[:200])
        return out.read_bytes()


def pdf_page_count(path: str | Path) -> int:
    doc = _open_pdf(path)
    try:
        return doc.page_count
    finally:
        doc.close()


def _open_pdf(path: str | Path) -> pymupdf.Document:
    p = _existing(path)
    try:
        doc = pymupdf.open(str(p))
    except (pymupdf.FileDataError, RuntimeError, ValueError) as exc:
        raise BodhanError("UNSUPPORTED_FORMAT", f"{p.name} is not a readable PDF.", hint=str(exc)[:200]) from exc
    if doc.needs_pass:
        doc.close()
        raise BodhanError("PDF_ENCRYPTED", f"{p} is password-protected.", hint="Supply an unencrypted copy.")
    return doc


def render_pdf_pages(
    path: str | Path, dpi: int = 300, max_edge_px: int = OCR_MAX_EDGE_PX
) -> Iterator[tuple[int, bytes]]:
    """Yield `(page_index, jpeg_bytes)` (zero-based), long edge clamped to `max_edge_px`."""
    doc = _open_pdf(path)  # validated eagerly; failures surface here, not on first next()
    return _iter_pages(doc, dpi, max_edge_px)


def _iter_pages(doc: pymupdf.Document, dpi: int, max_edge_px: int) -> Iterator[tuple[int, bytes]]:
    try:
        for index, page in enumerate(doc):
            longest_pt = max(page.rect.width, page.rect.height) or 1.0
            zoom = min(dpi / 72.0, max_edge_px / longest_pt)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False)
            yield index, pix.tobytes("jpeg", jpg_quality=90)
    finally:
        doc.close()


def image_to_jpeg(path: str | Path, max_edge_px: int = OCR_MAX_EDGE_PX) -> bytes:
    """JPEG q=90 bytes for png/jpg/jpeg/webp/tif, long edge clamped."""
    p = _existing(path)
    if p.suffix.lower() not in _IMAGE_SUFFIXES:
        raise BodhanError(
            "UNSUPPORTED_FORMAT",
            f"{p.suffix or p.name} is not a supported image type.",
            hint="Supported: png, jpg, jpeg, webp, tif.",
        )
    try:
        with Image.open(p) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((max_edge_px, max_edge_px), Image.LANCZOS)
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=90)
            return buffer.getvalue()
    except (OSError, ValueError) as exc:
        raise BodhanError("UNSUPPORTED_FORMAT", f"{p.name} could not be decoded as an image.", hint=str(exc)[:200]) from exc


def concat_wavs(paths: list[Path | str], out_path: str | Path, gap_ms: int = 120) -> Path:
    """Join PCM clips sample-accurately with `gap_ms` of silence at each seam."""
    files = [str(Path(p)) for p in paths]
    if not files:
        raise BodhanError("EMPTY_AUDIO", "No audio clips to join.", hint="Pass at least one WAV path.")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    gap = max(0, int(gap_ms)) / 1000.0
    chains = []
    for i in range(len(files)):
        pad = f",apad=pad_dur={gap:.3f}" if gap and i < len(files) - 1 else ""
        chains.append(
            f"[{i}:a]aresample=24000,aformat=sample_fmts=s16:channel_layouts=mono{pad}[a{i}]"
        )
    chains.append("".join(f"[a{i}]" for i in range(len(files))) + f"concat=n={len(files)}:v=0:a=1[out]")
    cmd = ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-v", "error"]
    for f in files:
        cmd += ["-i", f]
    cmd += ["-filter_complex", ";".join(chains), "-map", "[out]", "-c:a", "pcm_s16le", str(out)]
    proc = _run(cmd)
    if proc.returncode != 0 or not out.is_file() or out.stat().st_size <= 44:
        raise BodhanError("INTERNAL", "Could not join the audio clips.", hint=proc.stderr.strip()[:200])
    return out


def encode_mp3(wav_path: str | Path, mp3_path: str | Path) -> Path:
    wav = _existing(wav_path)
    out = Path(mp3_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = _run(
        ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-v", "error", "-i", str(wav),
         "-codec:a", "libmp3lame", "-b:a", "128k", str(out)]
    )
    if proc.returncode != 0 or not out.is_file():
        raise BodhanError("INTERNAL", "Could not encode MP3.", hint=proc.stderr.strip()[:200])
    return out


def ffprobe_duration(path: str | Path) -> float:
    """Duration in seconds of any decodable audio/video file."""
    return probe_audio(path)["duration_s"] or 0.0


def render_pdf_page(path: str | Path, index: int, dpi: int = 300, max_edge_px: int = OCR_MAX_EDGE_PX) -> bytes:
    """JPEG bytes for one zero-based page; the document is opened and closed per call, so pages can be cached independently."""
    doc = _open_pdf(path)
    try:
        page = doc[index]
        longest_pt = max(page.rect.width, page.rect.height) or 1.0
        zoom = min(dpi / 72.0, max_edge_px / longest_pt)
        return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False).tobytes("jpeg", jpg_quality=90)
    finally:
        doc.close()
