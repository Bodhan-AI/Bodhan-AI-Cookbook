"""Audio validation, FFmpeg normalisation and segment splitting (stdlib + ffmpeg binary)."""
from __future__ import annotations

import array
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import AppError, PayloadTooLarge
from app.services.audio.wav import WavFormat, WavFormatError, build_wav, parse_wav

logger = logging.getLogger("heritagelens.audio")

NORMALISED_RATE = 16000  # mono PCM16; the docs do not state a required rate (assumption).


class FFmpegNotAvailable(AppError):
    status_code = 503
    code = "ffmpeg_unavailable"
    message = ("FFmpeg is required to process audio but was not found. "
               "Install it and make sure it is on PATH, or set FFMPEG_PATH.")


class AudioProcessingError(AppError):
    status_code = 422
    code = "audio_processing_failed"
    message = "The audio file could not be processed. It may be corrupted or in an unsupported format."


def sniff_audio_format(head: bytes) -> str | None:
    """Identify audio by magic bytes (never by extension or declared MIME)."""
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"  # EBML: WebM/Matroska
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "m4a"
    if head[:5] == b"#!AMR":
        return "amr"
    if head[:3] == b"ID3":
        return "mp3"
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        version, layer = (head[1] >> 3) & 3, (head[1] >> 1) & 3
        if version != 1 and layer != 0:  # layer 0 would be ADTS AAC, which we do not claim
            return "mp3"
    return None


def find_ffmpeg(configured: str | None = None) -> str:
    candidate = configured or os.environ.get("FFMPEG_PATH")
    if candidate:
        resolved = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
        if resolved:
            return resolved
        raise FFmpegNotAvailable(f"FFMPEG_PATH is set but no executable was found at '{candidate}'.")
    found = shutil.which("ffmpeg")
    if not found:
        raise FFmpegNotAvailable()
    return found


def ffmpeg_version(configured: str | None = None) -> str | None:
    try:
        path = find_ffmpeg(configured)
        out = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10)
        return out.stdout.splitlines()[0] if out.returncode == 0 and out.stdout else None
    except Exception:
        return None


def convert_to_wav(src: Path, dst: Path, *, ffmpeg: str, sample_rate: int = NORMALISED_RATE,
                   timeout: float = 300.0) -> None:
    """Decode any FFmpeg-readable audio to mono PCM16 WAV. Raises AudioProcessingError on failure."""
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
           "-vn", "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(dst)]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise AudioProcessingError("Audio conversion timed out.") from exc
    except OSError as exc:
        raise FFmpegNotAvailable() from exc
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size < 44:
        logger.warning("ffmpeg failed rc=%s stderr=%s", proc.returncode,
                       proc.stderr.decode("utf-8", "replace")[-300:])
        raise AudioProcessingError()


@dataclass(frozen=True)
class AudioSegment:
    index: int
    start_seconds: float
    end_seconds: float
    wav_bytes: bytes

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


def _frame_energy(frames: bytes, start: int, end: int, bpf: int) -> int:
    chunk = frames[start * bpf: end * bpf]
    samples = array.array("h")
    samples.frombytes(chunk[: len(chunk) - len(chunk) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    return sum(abs(s) for s in samples)


def plan_cuts(frames: bytes, fmt: WavFormat, max_seconds: float, *,
              search_seconds: float = 3.0, probe_ms: int = 20) -> list[tuple[int, int]]:
    """Return contiguous (start_frame, end_frame) ranges covering every frame exactly once,
    each no longer than max_seconds, preferring to cut at the quietest nearby moment."""
    bpf = fmt.bytes_per_frame
    total = len(frames) // bpf
    max_frames = int(max_seconds * fmt.frame_rate)
    if max_frames < 1:
        raise ValueError("max_seconds too small")
    probe = max(1, int(fmt.frame_rate * probe_ms / 1000))
    search = int(search_seconds * fmt.frame_rate)
    ranges: list[tuple[int, int]] = []
    pos = 0
    while pos < total:
        if total - pos <= max_frames:
            ranges.append((pos, total))
            break
        hard_end = pos + max_frames
        lo = max(pos + max_frames // 2, hard_end - search)  # never shrink a segment below half
        best_cut, best_energy = hard_end, None
        cut = lo
        while cut + probe <= hard_end:
            e = _frame_energy(frames, cut, cut + probe, bpf)
            if best_energy is None or e < best_energy:
                best_cut, best_energy = cut, e
            cut += probe
        ranges.append((pos, best_cut))
        pos = best_cut
    return ranges


def split_wav(wav_path: Path, max_seconds: float) -> list[AudioSegment]:
    try:
        fmt, frames = parse_wav(wav_path.read_bytes())
    except WavFormatError as exc:
        raise AudioProcessingError() from exc
    total = len(frames) // fmt.bytes_per_frame
    if total == 0:
        raise AudioProcessingError("The audio contains no sound data.")
    segments = []
    bpf = fmt.bytes_per_frame
    for i, (s, e) in enumerate(plan_cuts(frames, fmt, max_seconds)):
        segments.append(AudioSegment(i, s / fmt.frame_rate, e / fmt.frame_rate,
                                     build_wav(fmt, frames[s * bpf: e * bpf])))
    return segments


def prepare_segments(src: Path, *, ffmpeg_path: str | None = None, max_segment_seconds: float = 29.0,
                     max_total_seconds: float = 3600.0, timeout: float = 300.0) -> list[AudioSegment]:
    """Normalise any supported audio to WAV and split into ordered segments <= max_segment_seconds."""
    if max_segment_seconds > 30.0:
        raise ValueError("Bodhan accepts at most 30 seconds per request.")
    ffmpeg = find_ffmpeg(ffmpeg_path)
    with tempfile.TemporaryDirectory(prefix="hl_audio_") as tmp:
        wav = Path(tmp) / "normalised.wav"
        convert_to_wav(src, wav, ffmpeg=ffmpeg, timeout=timeout)
        fmt, frames = parse_wav(wav.read_bytes())
        duration = len(frames) / fmt.bytes_per_frame / fmt.frame_rate
        if duration > max_total_seconds:
            raise PayloadTooLarge(f"Audio is {duration/60:.1f} minutes long; the limit is "
                                  f"{max_total_seconds/60:.0f} minutes.")
        segments = split_wav(wav, max_segment_seconds)
    covered = sum(s.duration_seconds for s in segments)
    if abs(covered - duration) > 0.01:  # defensive: never silently lose audio
        raise AudioProcessingError("Internal error: audio segments do not cover the recording.")
    return segments


def probe_duration_seconds(src: Path, *, ffmpeg_path: str | None = None, timeout: float = 300.0) -> float:
    """Validate that a file decodes and return its duration."""
    ffmpeg = find_ffmpeg(ffmpeg_path)
    with tempfile.TemporaryDirectory(prefix="hl_probe_") as tmp:
        wav = Path(tmp) / "probe.wav"
        convert_to_wav(src, wav, ffmpeg=ffmpeg, timeout=timeout)
        fmt, frames = parse_wav(wav.read_bytes())
    seconds = len(frames) / fmt.bytes_per_frame / fmt.frame_rate
    if seconds <= 0:
        raise AudioProcessingError("The audio contains no sound data.")
    return seconds
