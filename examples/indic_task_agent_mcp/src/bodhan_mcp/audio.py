"""Audio plumbing: probing, decoding, silence-aware windowing, joining, loudness and MP3 encoding.

#Plan
- ffmpeg decodes anything (WAV, MP3, M4A, MP4, WebM...) to 16 kHz mono PCM, so the 415 errors Indic-Transcribe
  gives for M4A/WebM never reach the caller.
- A simple energy VAD (allowed by the brief) finds silences. Windows are at most 28 s (2 s under the 30 s limit)
  and end in the longest silence available, so sentences are not cut. Only when someone speaks for 28 s without a
  pause do we hard-cut, with 0.5 s overlap so no word is lost. Windows with no speech are skipped, never sent.
- Synthesized chunks are joined with short pauses, loudness-normalised to -16 LUFS and encoded as MP3.
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

from .errors import ToolError

SR = 16_000
FRAME_S = 0.03
MAX_WINDOW_S = 28.0
MIN_CUT_S = 8.0
HARD_CUT_OVERLAP_S = 0.5


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise ToolError("internal_error", "ffmpeg is not installed.", hint="Install ffmpeg (brew install ffmpeg / apt install ffmpeg).")
    return exe


def probe(path: Path) -> dict:
    """Duration and stream kinds via ffprobe; empty dict if it cannot be read."""
    exe = shutil.which("ffprobe")
    if not exe:
        return {}
    proc = subprocess.run(
        [exe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        return {}
    try:
        info = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {}
    kinds = {s.get("codec_type") for s in info.get("streams", [])}
    duration = info.get("format", {}).get("duration")
    return {"duration_s": float(duration) if duration not in (None, "N/A") else None,
            "has_audio": "audio" in kinds, "has_video": "video" in kinds}


def decode_to_pcm(path: Path) -> np.ndarray:
    """Any media file to 16 kHz mono int16 samples."""
    proc = subprocess.run(
        [_ffmpeg(), "-v", "error", "-nostdin", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
        capture_output=True, timeout=600,
    )
    if proc.returncode != 0 or not proc.stdout:
        msg = proc.stderr.decode(errors="ignore").strip().splitlines()[-1:] or ["no audio stream"]
        raise ToolError("unsupported_format", f"Could not decode audio from {path.name}: {msg[0]}",
                        hint="Pass an audio or video file that contains an audio track.")
    return np.frombuffer(proc.stdout, dtype=np.int16)


def wav_bytes(samples: np.ndarray, sr: int = SR) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(samples.astype(np.int16).tobytes())
    return buf.getvalue()


def read_wav_bytes(data: bytes) -> tuple[np.ndarray, int]:
    """Read PCM16 WAV bytes (Indic-Speak returns 24 kHz mono PCM16); fall back to ffmpeg for anything else."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            if w.getsampwidth() == 2:
                frames = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
                if w.getnchannels() > 1:
                    frames = frames.reshape(-1, w.getnchannels()).mean(axis=1).astype(np.int16)
                return frames, w.getframerate()
    except (wave.Error, EOFError):
        pass
    proc = subprocess.run([_ffmpeg(), "-v", "error", "-i", "pipe:0", "-ac", "1", "-ar", "24000", "-f", "s16le", "-"],
                          input=data, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise ToolError("upstream_error", "Speech audio could not be decoded.", retryable=True)
    return np.frombuffer(proc.stdout, dtype=np.int16), 24_000


def resample(samples: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    if sr_from == sr_to or len(samples) == 0:
        return samples
    n = int(round(len(samples) * sr_to / sr_from))
    x_old = np.linspace(0, 1, num=len(samples), endpoint=False)
    x_new = np.linspace(0, 1, num=n, endpoint=False)
    return np.interp(x_new, x_old, samples.astype(np.float64)).astype(np.int16)


# --------------------------------------------------------------------------- VAD and windowing

def frame_energy_db(samples: np.ndarray, sr: int = SR) -> np.ndarray:
    hop = int(sr * FRAME_S)
    n = len(samples) // hop
    if n == 0:
        return np.array([-100.0])
    frames = samples[: n * hop].astype(np.float64).reshape(n, hop) / 32768.0
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    return 20 * np.log10(rms)


def speech_mask(samples: np.ndarray, sr: int = SR) -> np.ndarray:
    """True for frames that look like speech. Threshold adapts to the recording's noise floor."""
    db = frame_energy_db(samples, sr)
    floor, peak = np.percentile(db, 10), np.percentile(db, 95)
    # 10 dB above the noise floor, but never so high that steady speech with no pauses counts as silence
    threshold = max(-50.0, min(floor + 10.0, peak - 20.0))
    mask = db > threshold
    # close tiny gaps (< 150 ms) so a word's internal pauses do not count as silence
    gap = int(0.15 / FRAME_S)
    idx = np.where(mask)[0]
    for a, b in zip(idx[:-1], idx[1:], strict=False):
        if 1 < b - a <= gap:
            mask[a:b] = True
    return mask


@dataclass
class Window:
    start_s: float
    end_s: float
    speech_start_s: float
    speech_end_s: float
    has_speech: bool
    hard_cut: bool = False


def plan_windows(samples: np.ndarray, sr: int = SR, max_s: float = MAX_WINDOW_S, min_cut_s: float = MIN_CUT_S) -> list[Window]:
    """Split audio into windows of at most `max_s`, cutting at the longest silence available."""
    mask = speech_mask(samples, sr)
    total_s = len(samples) / sr
    fps = 1.0 / FRAME_S
    windows: list[Window] = []
    start = 0.0
    while start < total_s - 1e-6:
        if total_s - start <= max_s:
            end, hard = total_s, False
        else:
            lo, hi = int((start + min_cut_s) * fps), int((start + max_s) * fps)
            seg = mask[lo:hi]
            best_len, best_mid = 0, None
            run_start = None
            for i, voiced in enumerate(np.append(seg, True)):
                if not voiced and run_start is None:
                    run_start = i
                elif voiced and run_start is not None:
                    length = i - run_start
                    if length >= best_len:  # ties go to the later silence: longer windows, fewer calls
                        best_len, best_mid = length, run_start + length // 2
                    run_start = None
            if best_mid is not None and best_len >= 3:  # at least ~90 ms of silence
                end, hard = (lo + best_mid) / fps, False
            else:
                end, hard = start + max_s, True
        f0, f1 = int(start * fps), max(int(start * fps) + 1, int(end * fps))
        voiced_idx = np.where(mask[f0:f1])[0]
        has_speech = len(voiced_idx) >= int(0.25 * fps)  # at least 250 ms of speech
        sp0 = start + (voiced_idx[0] / fps if len(voiced_idx) else 0.0)
        sp1 = start + ((voiced_idx[-1] + 1) / fps if len(voiced_idx) else 0.0)
        windows.append(Window(round(start, 3), round(end, 3), round(sp0, 3), round(min(sp1, end), 3), has_speech, hard))
        start = end - HARD_CUT_OVERLAP_S if hard else end
    return windows


def slice_samples(samples: np.ndarray, start_s: float, end_s: float, sr: int = SR) -> np.ndarray:
    return samples[int(start_s * sr): int(end_s * sr)]


# --------------------------------------------------------------------------- output

def join_with_pauses(chunks: list[tuple[np.ndarray, int]], pause_s: float = 0.25, sr: int = 24_000) -> np.ndarray:
    pause = np.zeros(int(pause_s * sr), dtype=np.int16)
    parts: list[np.ndarray] = []
    for i, (samples, chunk_sr) in enumerate(chunks):
        if i:
            parts.append(pause)
        parts.append(resample(samples, chunk_sr, sr))
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16)


def encode_mp3(samples: np.ndarray, sr: int, out_path: Path, normalize: bool = True) -> None:
    """Loudness-normalise to -16 LUFS (single pass) and write MP3."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    filters = ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"] if normalize and len(samples) > sr else []
    proc = subprocess.run(
        [_ffmpeg(), "-v", "error", "-y", "-f", "s16le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0", *filters,
         "-ar", str(sr), "-codec:a", "libmp3lame", "-q:a", "3", str(out_path)],
        input=samples.astype(np.int16).tobytes(), capture_output=True, timeout=300,
    )
    if proc.returncode != 0:
        raise ToolError("internal_error", f"MP3 encoding failed: {proc.stderr.decode(errors='ignore')[-200:]}")


def measure_lufs(path: Path) -> float | None:
    proc = subprocess.run([_ffmpeg(), "-v", "info", "-nostats", "-i", str(path), "-af", "ebur128", "-f", "null", "-"],
                          capture_output=True, text=True, timeout=300)
    for line in reversed(proc.stderr.splitlines()):
        line = line.strip()
        if line.startswith("I:") and "LUFS" in line:
            try:
                return float(line.split()[1])
            except (IndexError, ValueError):
                return None
    return None
