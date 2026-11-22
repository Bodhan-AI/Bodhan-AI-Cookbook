"""Audio plumbing: decoding, pause detection, chunking and export.

All heavy lifting is ffmpeg plus a little numpy. No speech model is used here;
pause detection is a plain energy threshold (voice-activity detection).
"""

from __future__ import annotations

import io
import json
import subprocess
import wave
from pathlib import Path

import numpy as np

from .errors import ToolError

AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wma", ".amr"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
TEXT_EXT = {".txt", ".md", ".markdown", ".srt", ".vtt", ".json", ".csv"}


def kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in AUDIO_EXT:
        return "audio"
    if ext in VIDEO_EXT:
        return "video"
    if ext in IMAGE_EXT:
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext in TEXT_EXT:
        return "text"
    return "unknown"


def _run(cmd: list[str], timeout: float = 300.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError:
        raise ToolError("internal_error", f"'{cmd[0]}' is not installed.", hint="Install ffmpeg (it provides ffmpeg and ffprobe).") from None
    except subprocess.TimeoutExpired:
        raise ToolError("timeout", f"{cmd[0]} took longer than {timeout:.0f} s.", retryable=True) from None


def probe(path: Path) -> dict:
    """Duration and stream types of a media file, from ffprobe."""
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)], timeout=60)
    if out.returncode != 0:
        raise ToolError("unsupported_file_type", f"'{path.name}' could not be read as audio or video.", hint="Check that the file is not empty or corrupted.")
    info = json.loads(out.stdout or b"{}")
    kinds = {s.get("codec_type") for s in info.get("streams", [])}
    duration = float(info.get("format", {}).get("duration") or 0.0)
    return {"duration_s": round(duration, 3), "has_audio": "audio" in kinds, "has_video": "video" in kinds}


def decode_to_pcm(path: Path, rate: int = 16000) -> np.ndarray:
    """Decode any audio or video file to mono int16 samples at `rate` Hz."""
    out = _run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"])
    if out.returncode != 0 or not out.stdout:
        raise ToolError("unsupported_file_type", f"'{path.name}' has no audio that ffmpeg can decode.", hint="Pass an audio or video file with an audio track.")
    return np.frombuffer(out.stdout, dtype=np.int16)


def pcm_to_wav(samples: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(samples.astype(np.int16).tobytes())
    return buf.getvalue()


def wav_to_pcm(data: bytes) -> tuple[np.ndarray, int]:
    """Read WAV bytes into mono int16 samples. Raises ToolError if it is not PCM16 WAV."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            rate, channels, width = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
            frames = wf.readframes(wf.getnframes())
    except (wave.Error, EOFError):
        raise ToolError("upstream_error", "The speech model returned audio that is not a valid WAV file.", retryable=True) from None
    if width != 2:
        raise ToolError("upstream_error", f"Expected 16-bit audio from the speech model, got {8 * width}-bit.")
    samples = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return samples, rate


def frame_energy(samples: np.ndarray, rate: int, frame_s: float = 0.02) -> np.ndarray:
    """Root-mean-square loudness of each 20 ms frame."""
    n = max(1, int(rate * frame_s))
    usable = len(samples) // n * n
    if usable == 0:
        return np.zeros(0)
    frames = samples[:usable].astype(np.float64).reshape(-1, n)
    return np.sqrt((frames**2).mean(axis=1))


def speech_windows(samples: np.ndarray, rate: int, max_s: float = 28.0, min_silence_s: float = 0.3, frame_s: float = 0.02) -> list[tuple[float, float]]:
    """Split audio into windows of speech, each at most `max_s` long.

    Steps:
      1. Mark each 20 ms frame as speech or silence with an energy threshold
         set relative to this recording's own noise floor.
      2. Join speech frames into regions, bridging pauses shorter than `min_silence_s`.
      3. Pack neighbouring regions into windows up to `max_s`, so cuts fall in pauses.
      4. If one region is longer than `max_s`, cut it at its quietest frame.
    Silent stretches are never returned, so they are never sent to the model.
    """
    energy = frame_energy(samples, rate, frame_s)
    if len(energy) == 0:
        return []
    floor = np.percentile(energy, 10)
    peak = np.percentile(energy, 95)
    if peak < 50:  # digital silence throughout
        return []
    # Three times the noise floor, but never above a quarter of the loud level: in a recording
    # that is almost all speech, the "floor" is speech too and must not set the bar.
    threshold = max(40.0, peak * 0.08, min(floor * 3.0, peak * 0.25))
    is_speech = energy > threshold

    # 1-2: regions of speech, bridging short pauses and padding the edges a little
    regions: list[list[int]] = []
    gap_frames = int(min_silence_s / frame_s)
    start = None
    last_speech = -1
    for i, flag in enumerate(is_speech):
        if flag:
            if start is None:
                start = i
            last_speech = i
        elif start is not None and i - last_speech > gap_frames:
            regions.append([start, last_speech + 1])
            start = None
    if start is not None:
        regions.append([start, last_speech + 1])
    pad = int(0.1 / frame_s)
    regions = [[max(0, a - pad), min(len(energy), b + pad)] for a, b in regions if (b - a) * frame_s >= 0.15]
    if not regions:
        return []

    # 4: cut any over-long region at its quietest point in the second half of the window
    max_frames = int(max_s / frame_s)
    pieces: list[list[int]] = []
    for a, b in regions:
        while b - a > max_frames:
            lo, hi = a + max_frames // 2, a + max_frames
            cut = lo + int(np.argmin(energy[lo:hi]))
            pieces.append([a, cut])
            a = cut
        pieces.append([a, b])

    # 3: pack neighbours into windows no longer than max_s
    windows: list[list[int]] = [pieces[0][:]]
    for a, b in pieces[1:]:
        if b - windows[-1][0] <= max_frames:
            windows[-1][1] = b
        else:
            windows.append([a, b])
    return [(round(a * frame_s, 2), round(b * frame_s, 2)) for a, b in windows]


def audio_health(samples: np.ndarray, rate: int) -> dict:
    """Cheap sanity checks on generated speech: clipping and long silences."""
    if len(samples) == 0:
        return {"duration_s": 0.0, "clipped_share": 0.0, "longest_silence_s": 0.0}
    energy = frame_energy(samples, rate)
    quiet = energy < 40.0
    longest = run = 0
    for flag in quiet:
        run = run + 1 if flag else 0
        longest = max(longest, run)
    return {
        "duration_s": round(len(samples) / rate, 2),
        "clipped_share": round(float((np.abs(samples.astype(np.int32)) >= 32700).mean()), 4),
        "longest_silence_s": round(longest * 0.02, 2),
    }


def export_audio(samples: np.ndarray, rate: int, out_path: Path) -> None:
    """Loudness-normalise to about -16 LUFS and write WAV or MP3, labelled as AI-generated."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    codec = ["-c:a", "libmp3lame", "-q:a", "4"] if out_path.suffix.lower() == ".mp3" else ["-c:a", "pcm_s16le"]
    cmd = [
        "ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "-",
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", str(rate),
        "-metadata", "comment=AI-generated speech (Bodhan Indic-Speak)", *codec, str(out_path),
    ]
    try:
        proc = subprocess.run(cmd, input=samples.astype(np.int16).tobytes(), capture_output=True, timeout=300, check=False)
    except FileNotFoundError:
        raise ToolError("internal_error", "'ffmpeg' is not installed.", hint="Install ffmpeg.") from None
    if proc.returncode != 0:
        raise ToolError("internal_error", f"ffmpeg could not write {out_path.name}: {proc.stderr.decode(errors='replace')[-300:]}")


def render_pdf_pages(path: Path, pages: list[int] | None, dpi: int = 170) -> list[tuple[int, bytes]]:
    """Render PDF pages to PNG bytes. Page numbers start at 1."""
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(str(path))
    except Exception:  # noqa: BLE001 - pdfium raises several error types
        raise ToolError("unsupported_file_type", f"'{path.name}' is not a readable PDF.") from None
    total = len(doc)
    wanted = pages or list(range(1, total + 1))
    out = []
    for number in wanted:
        if not 1 <= number <= total:
            raise ToolError("invalid_arguments", f"Page {number} does not exist; the PDF has {total} pages.", hint=f"Use pages between 1 and {total}.")
        image = doc[number - 1].render(scale=dpi / 72).to_pil().convert("RGB")
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        out.append((number, buf.getvalue()))
    return out


def pdf_page_count(path: Path) -> int:
    import pypdfium2 as pdfium

    try:
        return len(pdfium.PdfDocument(str(path)))
    except Exception:  # noqa: BLE001
        raise ToolError("unsupported_file_type", f"'{path.name}' is not a readable PDF.") from None


def image_to_png(path: Path, max_side: int = 2400) -> bytes:
    """Load any image, shrink very large photos, and return PNG bytes."""
    from PIL import Image, ImageOps

    try:
        image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    except Exception:  # noqa: BLE001 - Pillow raises several error types
        raise ToolError("unsupported_file_type", f"'{path.name}' is not a readable image.") from None
    if max(image.size) > max_side:
        image.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
