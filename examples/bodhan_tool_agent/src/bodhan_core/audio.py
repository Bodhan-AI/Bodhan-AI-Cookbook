"""Audio plumbing: ffprobe/ffmpeg wrappers, energy-based cut planning, WAV slicing and joining.

Cut planning is pure (energies in, cut list out) so the 30-second window rule is unit-tested without ffmpeg.
"""

from __future__ import annotations

import io
import json
import math
import shutil
import subprocess
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

from .errors import ErrorCode, ToolError

TARGET_RATE = 16_000
FRAME_MS = 20
MAX_WINDOW_S = 29.5  # Indic-Transcribe rejects requests over 30 s; keep a margin
OVERLAP_S = 0.6  # overlap added on hard cuts (no silence found) so no word is lost


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise ToolError(
                ErrorCode.INTERNAL, f"{tool} is not installed or not on PATH.",
                hint="Install ffmpeg (brew install ffmpeg / apt-get install ffmpeg).",
            )


def _run(cmd: list[str], what: str) -> subprocess.CompletedProcess:
    require_ffmpeg()
    proc = subprocess.run(cmd, capture_output=True, timeout=600)
    if proc.returncode != 0:
        tail = proc.stderr.decode(errors="replace").strip().splitlines()[-3:]
        raise ToolError(ErrorCode.UNSUPPORTED_MEDIA, f"{what} failed: {' | '.join(tail)}",
                        hint="Check the file is a valid audio/video file.")
    return proc


def probe(path: Path) -> dict:
    """Container/stream facts from ffprobe: duration, audio/video presence, codec, rate, channels."""
    proc = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                f"Reading {path.name}")
    info = json.loads(proc.stdout or b"{}")
    streams = info.get("streams", [])
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    video = next((s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1),
                 None)
    duration = float(info.get("format", {}).get("duration") or (audio or {}).get("duration") or 0)
    return {
        "duration_s": round(duration, 3),
        "format": info.get("format", {}).get("format_name"),
        "has_audio": audio is not None,
        "has_video": video is not None,
        "audio_codec": (audio or {}).get("codec_name"),
        "sample_rate": int((audio or {}).get("sample_rate") or 0) or None,
        "channels": (audio or {}).get("channels"),
    }


def to_wav16k(src: Path, dst: Path) -> Path:
    """Decode any audio or video file to 16 kHz mono PCM16 WAV (drops video)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", str(TARGET_RATE),
          "-sample_fmt", "s16", str(dst)], f"Decoding {src.name}")
    return dst


def read_pcm(path_or_bytes: Path | bytes) -> tuple[array, int]:
    src = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else str(path_or_bytes)
    with wave.open(src) as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ToolError(ErrorCode.UNSUPPORTED_MEDIA, "Expected mono PCM16 WAV.", hint="Decode with to_wav16k first.")
        samples = array("h")
        samples.frombytes(w.readframes(w.getnframes()))
        return samples, w.getframerate()


def wav_bytes(samples: array, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


def wav_duration(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / w.getframerate()


def frame_energies(samples: array, rate: int, frame_ms: int = FRAME_MS) -> list[float]:
    """RMS energy per frame."""
    n = max(int(rate * frame_ms / 1000), 1)
    out = []
    for start in range(0, len(samples), n):
        frame = samples[start : start + n]
        out.append(math.sqrt(sum(x * x for x in frame) / len(frame)) if frame else 0.0)
    return out


@dataclass(frozen=True)
class Window:
    start_s: float
    end_s: float
    hard_cut_before: bool = False  # True when this window starts at a non-silent cut (has overlap)
    silent: bool = False  # True when the whole window is silence (skip it: no speech, no cost)

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


def plan_windows(
    energies: list[float],
    *,
    frame_ms: int = FRAME_MS,
    max_s: float = MAX_WINDOW_S,
    min_s: float | None = None,
    overlap_s: float = OVERLAP_S,
) -> list[Window]:
    """Cut audio into windows of at most ``max_s`` seconds, cutting at the quietest point.

    The cut for each window is searched between ``min_s`` (default 2/3 of max) and ``max_s`` after its
    start, and lands on the lowest smoothed energy there. A cut is "hard" when even that point is
    louder than the silence threshold; the next window then starts ``overlap_s`` early so no word is
    lost (the transcript stitcher removes the duplicated words).
    """
    if not energies:
        return []
    frame_s = frame_ms / 1000
    total = len(energies) * frame_s
    min_s = max_s * 2 / 3 if min_s is None else min_s
    smooth = _smooth(energies, radius=5)
    threshold = _silence_threshold(energies)

    windows: list[Window] = []
    start = 0.0
    hard = False
    while total - start > max_s:
        lo = int((start + min_s) / frame_s)
        hi = int((start + max_s) / frame_s)
        silent_frames = [f for f in range(lo, hi) if smooth[f] <= threshold]
        if silent_frames:
            cut_frame = silent_frames[-1]  # latest pause: longest window that still cuts in silence
        else:
            cut_frame = min(range(lo, hi), key=lambda f: smooth[f])
        cut = cut_frame * frame_s
        windows.append(Window(round(start, 3), round(cut, 3), hard))
        hard = not silent_frames
        start = max(cut - overlap_s, 0.0) if hard else cut
    windows.append(Window(round(start, 3), round(total, 3), hard))

    marked = []
    for w in windows:
        a, b = int(w.start_s / frame_s), max(int(w.end_s / frame_s), int(w.start_s / frame_s) + 1)
        silent = max(energies[a:b], default=0.0) <= threshold
        marked.append(Window(w.start_s, w.end_s, w.hard_cut_before, silent))
    return marked


def _smooth(values: list[float], radius: int) -> list[float]:
    out = []
    acc = 0.0
    window: list[float] = []
    padded = [values[0]] * radius + values + [values[-1]] * radius
    for i, v in enumerate(padded):
        window.append(v)
        acc += v
        if len(window) > 2 * radius + 1:
            acc -= window.pop(0)
        if i >= 2 * radius:
            out.append(acc / len(window))
    return out


def _silence_threshold(energies: list[float]) -> float:
    """Energy below which a frame counts as silence.

    Twice the noise floor (5th percentile), capped at 30% of the loud level (90th percentile) so that
    uniformly loud audio has no "silence", and never below an absolute floor for digital silence.
    """
    ranked = sorted(energies)
    loud = ranked[int(len(ranked) * 0.9)] if ranked else 0.0
    floor = ranked[int(len(ranked) * 0.05)] if ranked else 0.0
    return max(min(floor * 2, loud * 0.3), loud * 0.05, 50.0)


def slice_wav(samples: array, rate: int, start_s: float, end_s: float) -> bytes:
    return wav_bytes(samples[int(start_s * rate) : int(end_s * rate)], rate)


def concat_wavs(clips: list[bytes], gap_ms: int = 250) -> bytes:
    """Join mono PCM16 WAV clips of the same sample rate with short silences between them."""
    if not clips:
        raise ValueError("no clips to join")
    joined = array("h")
    rate = None
    for i, clip in enumerate(clips):
        samples, r = read_pcm(clip)
        if rate is None:
            rate = r
        elif r != rate:
            raise ToolError(ErrorCode.INTERNAL, f"Clip {i} has sample rate {r}, expected {rate}.")
        if i:
            joined.extend([0] * int(rate * gap_ms / 1000))
        joined.extend(samples)
    return wav_bytes(joined, rate)


def wav_to_mp3(wav: Path, mp3: Path, *, title: str | None = None, comment: str | None = None) -> Path:
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(wav), "-codec:a", "libmp3lame", "-q:a", "4"]
    if title:
        cmd += ["-metadata", f"title={title}"]
    if comment:
        cmd += ["-metadata", f"comment={comment}"]
    _run([*cmd, str(mp3)], f"Encoding {mp3.name}")
    return mp3
