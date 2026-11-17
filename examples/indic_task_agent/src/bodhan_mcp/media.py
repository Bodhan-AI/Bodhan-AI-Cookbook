"""Media plumbing in plain code: probing, decoding, voice-activity chunking, WAV assembly,
format conversion, PDF rendering and image preparation. Uses ffmpeg/ffprobe (system), numpy,
Pillow and pypdfium2. No recognition or synthesis happens here.
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

from .errors import BodhanToolError

AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wma", ".amr", ".webm"}
VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".3gp", ".flv", ".wmv", ".mpeg", ".mpg"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".gif"}
TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".srt", ".vtt", ".json", ".html", ".htm", ".rst"}
DOC_EXT = {".docx"}

SAMPLE_RATE = 16_000


def require_ffmpeg() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise BodhanToolError(
            "upstream_error",
            "ffmpeg/ffprobe are not installed on the server.",
            hint="Install ffmpeg (apt install ffmpeg / brew install ffmpeg) or use the Docker image.",
        )


def resolve_input(path: str) -> Path:
    if not path or not str(path).strip():
        raise BodhanToolError("invalid_input", "A file path is required.")
    p = Path(str(path).strip()).expanduser()
    if not p.exists():
        raise BodhanToolError(
            "file_not_found",
            f"No such file: {path}",
            hint="Pass an absolute path, or a path relative to the server's working directory.",
        )
    if not p.is_file():
        raise BodhanToolError("file_not_found", f"Not a regular file: {path}")
    return p.resolve()


def probe(path: Path) -> dict:
    require_ffmpeg()
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        return {}
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def media_info(path: Path) -> dict:
    info = probe(path)
    streams = info.get("streams") or []
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    has_video = any(
        s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1 for s in streams
    )
    try:
        duration = float((info.get("format") or {}).get("duration") or 0.0)
    except ValueError:
        duration = 0.0
    return {
        "has_audio": has_audio,
        "has_video": has_video,
        "duration_s": round(duration, 2),
        "format": (info.get("format") or {}).get("format_name"),
    }


def decode_audio(path: Path) -> np.ndarray:
    """Any audio/video file -> mono 16 kHz int16 samples (video tracks are ignored)."""
    require_ffmpeg()
    proc = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",
            "-",
        ],
        capture_output=True,
        timeout=600,
    )
    if proc.returncode != 0:
        msg = proc.stderr.decode(errors="replace")[-300:]
        if (
            "does not contain any stream" in msg
            or "Output file #0 does not contain" in msg
            or "matches no streams" in msg
        ):
            raise BodhanToolError("no_audio_stream", f"{path.name} has no audio track.")
        raise BodhanToolError("unsupported_file_type", f"Could not decode audio from {path.name}: {msg.strip()}")
    samples = np.frombuffer(proc.stdout, dtype=np.int16)
    if samples.size == 0:
        raise BodhanToolError("no_audio_stream", f"{path.name} has no decodable audio.")
    return samples


def wav_bytes(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(np.asarray(samples, dtype=np.int16).tobytes())
    return buf.getvalue()


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data), "rb") as wf:
        rate, width, channels = wf.getframerate(), wf.getsampwidth(), wf.getnchannels()
        frames = wf.readframes(wf.getnframes())
    if width != 2:
        raise BodhanToolError("upstream_error", f"Unexpected WAV sample width {width} from TTS.")
    samples = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return samples, rate


# ---------------------------------------------------------------------------- voice-activity chunking


@dataclass(frozen=True)
class Span:
    start: float  # seconds
    end: float
    speech_s: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def plan_chunks(
    samples: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    *,
    target_s: float = 20.0,
    min_s: float = 3.0,
    max_s: float = 28.0,
    frame_ms: int = 30,
    min_silence_ms: int = 200,
) -> list[Span]:
    """Split audio into chunks no longer than `max_s`, cutting inside pauses.

    Energy-based VAD: frames below an adaptive threshold (relative to the clip's own noise floor
    and peak) count as silence. We cut at the middle of the first pause after `target_s`; if no
    pause exists before `max_s`, at the quietest frame in the last 3 s before `max_s`. Chunks
    with almost no speech are marked (speech_s ~ 0) so callers can skip them and save money.
    """
    if max_s > 30:
        raise ValueError("Indic-Transcribe accepts at most 30 s per request")
    n = len(samples)
    frame = max(1, int(sample_rate * frame_ms / 1000))
    n_frames = max(1, int(np.ceil(n / frame)))
    padded = np.zeros(n_frames * frame, dtype=np.float32)
    padded[:n] = samples.astype(np.float32)
    rms = np.sqrt(np.mean(padded.reshape(n_frames, frame) ** 2, axis=1))
    floor = float(np.percentile(rms, 10))
    peak = float(np.percentile(rms, 95))
    # Adaptive threshold, capped at half the loud level so a clip with no pauses is not all "silence";
    # anything below an absolute floor (digital silence / hiss) is always silence.
    threshold = min(max(floor * 2.5, floor + 0.1 * (peak - floor)), 0.5 * peak)
    silent = (rms < threshold) | (rms < 40.0)

    # silence runs -> candidate cut points (frame index of each run's middle)
    min_run = max(1, min_silence_ms // frame_ms)
    cuts: list[int] = []
    i = 0
    while i < n_frames:
        if silent[i]:
            j = i
            while j < n_frames and silent[j]:
                j += 1
            if j - i >= min_run:
                cuts.append((i + j) // 2)
            i = j
        else:
            i += 1

    fps = 1000 / frame_ms
    spans: list[Span] = []
    start = 0
    total_frames = n_frames
    while start < total_frames:
        if (total_frames - start) / fps <= max_s:
            end = total_frames
        else:
            lo, tgt, hi = start + int(min_s * fps), start + int(target_s * fps), start + int(max_s * fps)
            candidates = [c for c in cuts if lo <= c <= hi]
            if candidates:
                end = min(candidates, key=lambda c: abs(c - tgt))  # the pause closest to the target length
            else:
                window_lo = max(lo, hi - int(3 * fps))
                end = window_lo + int(np.argmin(rms[window_lo:hi])) if hi > window_lo else hi
            end = max(end, start + 1)
        speech_frames = int(np.sum(~silent[start:end]))
        spans.append(
            Span(round(start / fps, 3), round(min(end / fps, n / sample_rate), 3), round(speech_frames / fps, 3))
        )
        start = end
    return spans


def slice_samples(samples: np.ndarray, span: Span, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    return samples[int(span.start * sample_rate) : int(span.end * sample_rate)]


# ---------------------------------------------------------------------------- output audio


def concat_wavs(parts: list[bytes], gap_s: float = 0.25) -> tuple[bytes, float]:
    """Join TTS clips (same rate) with a short pause between them. Returns (wav bytes, duration)."""
    arrays: list[np.ndarray] = []
    rate = None
    for data in parts:
        samples, r = read_wav(data)
        if rate is None:
            rate = r
        elif r != rate:
            raise BodhanToolError("upstream_error", "TTS returned clips with different sample rates.")
        arrays.append(samples)
        arrays.append(np.zeros(int(r * gap_s), dtype=np.int16))
    if not arrays:
        raise BodhanToolError("invalid_input", "Nothing to join.")
    joined = np.concatenate(arrays[:-1]) if len(arrays) > 1 else arrays[0]
    rate = rate or 24_000
    return wav_bytes(joined, rate), round(len(joined) / rate, 2)


def encode_audio(wav: bytes, dest: Path, fmt: str, title: str) -> Path:
    """Write WAV/MP3/OGG with metadata that labels the audio as AI-generated."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    comment = "AI-generated speech (Bodhan Indic-Speak). Not a human recording."
    if fmt == "wav":
        dest.write_bytes(wav)
        return dest
    require_ffmpeg()
    codec = {"mp3": ["-c:a", "libmp3lame", "-q:a", "3"], "ogg": ["-c:a", "libvorbis", "-q:a", "5"]}[fmt]
    proc = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "wav",
            "-i",
            "pipe:0",
            *codec,
            "-metadata",
            f"title={title}",
            "-metadata",
            f"comment={comment}",
            "-metadata",
            "artist=AI voice (Bodhan Indic-Speak)",
            str(dest),
        ],
        input=wav,
        capture_output=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise BodhanToolError(
            "upstream_error", f"ffmpeg could not encode {fmt}: {proc.stderr.decode(errors='replace')[-200:]}"
        )
    return dest


# ---------------------------------------------------------------------------- documents & images


def pdf_page_count(path: Path) -> int:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as exc:  # pypdfium2 raises PdfiumError for broken/encrypted files
        raise BodhanToolError("unsupported_file_type", f"Cannot open PDF {path.name}: {exc}") from exc
    try:
        return len(pdf)
    finally:
        pdf.close()


def render_pdf_page(path: Path, index: int, dpi: int = 200) -> tuple[bytes, str, bool]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(path))
    try:
        page = pdf[index]
        image = page.render(scale=dpi / 72).to_pil()
        page.close()
    finally:
        pdf.close()
    return prepare_image_obj(image)


def prepare_image_obj(image, max_side: int = 2800) -> tuple[bytes, str, bool]:
    """Return (bytes, mime, looks_blank). Downscales huge images; converts to PNG (or JPEG for big photos)."""
    from PIL import Image, ImageOps

    image = ImageOps.exif_transpose(image)
    if image.mode not in {"RGB", "L"}:
        image = image.convert("RGB")
    if max(image.size) > max_side:
        scale = max_side / max(image.size)
        image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
    gray = np.asarray(image.convert("L"), dtype=np.float32)
    looks_blank = float(gray.std()) < 2.0
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    data, mime = buf.getvalue(), "image/png"
    if len(data) > 4_000_000:
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="JPEG", quality=90)
        data, mime = buf.getvalue(), "image/jpeg"
    return data, mime, looks_blank


def load_image(path: Path) -> tuple[bytes, str, bool]:
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as im:
            im.load()
            return prepare_image_obj(im)
    except (UnidentifiedImageError, OSError) as exc:
        raise BodhanToolError("unsupported_file_type", f"Cannot read image {path.name}: {exc}") from exc


def read_text_file(path: Path) -> str:
    if path.suffix.lower() == ".docx":
        import docx  # python-docx

        document = docx.Document(str(path))
        parts = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                parts.append("| " + " | ".join(c.text.strip() for c in row.cells) + " |")
        return "\n".join(parts)
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def classify(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in IMAGE_EXT:
        return "image"
    if ext in DOC_EXT:
        return "document"
    if ext in TEXT_EXT:
        return "text"
    if ext in VIDEO_EXT and ext != ".webm":
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    info = media_info(path) if shutil.which("ffprobe") else {}
    if info.get("has_video"):
        return "video"
    if info.get("has_audio"):
        return "audio"
    try:
        path.read_bytes()[:4096].decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        return "unknown"
