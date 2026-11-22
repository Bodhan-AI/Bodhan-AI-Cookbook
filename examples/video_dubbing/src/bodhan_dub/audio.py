"""Audio plumbing for synthesis and mixing: decode, trim, time-stretch, loudness. ffmpeg + numpy only."""

from __future__ import annotations

import io
import re
import subprocess
import wave
from pathlib import Path

import numpy as np

MIX_RATE = 48_000


class AudioError(RuntimeError):
    pass


def _ffmpeg(args: list[str], stdin: bytes | None = None) -> bytes:
    proc = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", *args],
                          input=stdin, capture_output=True)
    if proc.returncode != 0:
        raise AudioError(f"ffmpeg failed: {proc.stderr.decode(errors='replace').strip()[-400:]}")
    return proc.stdout


def wav_to_array(data: bytes) -> tuple[np.ndarray, int]:
    """PCM16 WAV bytes -> mono float32 samples in [-1, 1] and the sample rate."""
    with wave.open(io.BytesIO(data), "rb") as wf:
        rate, channels = wf.getframerate(), wf.getnchannels()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    return pcm, rate


def array_to_wav(samples: np.ndarray, rate: int) -> bytes:
    """Mono or (n, channels) float32 -> PCM16 WAV bytes."""
    arr = np.asarray(samples, dtype=np.float32)
    channels = 1 if arr.ndim == 1 else arr.shape[1]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes((np.clip(arr, -1.0, 1.0) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def trim_silence(samples: np.ndarray, rate: int, threshold_db: float = -45.0, pad_s: float = 0.03) -> np.ndarray:
    """Cut leading/trailing silence that TTS adds, so a clip's length is its speech length."""
    if len(samples) == 0:
        return samples
    frame = max(1, int(rate * 0.01))
    n = len(samples) // frame
    if n == 0:
        return samples
    rms = np.sqrt(np.mean(samples[: n * frame].reshape(n, frame) ** 2, axis=1))
    loud = np.nonzero(20 * np.log10(rms + 1e-10) > threshold_db)[0]
    if len(loud) == 0:
        return samples[:0]
    pad = int(pad_s * rate)
    return samples[max(0, loud[0] * frame - pad): min(len(samples), (loud[-1] + 1) * frame + pad)]


def stretch_and_resample(samples: np.ndarray, rate: int, tempo: float, out_rate: int = MIX_RATE) -> np.ndarray:
    """Speed speech up by `tempo` (pitch kept, ffmpeg atempo) and resample to the mix rate."""
    filters = [f"atempo={tempo:.4f}"] if abs(tempo - 1.0) > 1e-3 else []
    filters.append(f"aresample={out_rate}")
    raw = _ffmpeg(["-f", "f32le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
                   "-af", ",".join(filters), "-f", "f32le", "-ac", "1", "pipe:1"],
                  stdin=np.asarray(samples, dtype=np.float32).tobytes())
    return np.frombuffer(raw, dtype=np.float32).copy()


def decode_audio(path: Path, rate: int = MIX_RATE, channels: int = 2) -> np.ndarray:
    """Decode a file's first audio stream to float32 shaped (n, channels)."""
    raw = _ffmpeg(["-i", str(path), "-map", "0:a:0", "-ac", str(channels), "-ar", str(rate), "-f", "f32le", "pipe:1"])
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, channels).copy()


_LUFS = re.compile(r"I:\s+(-?[\d.]+|-inf)\s+LUFS")


def integrated_lufs(samples: np.ndarray, rate: int = MIX_RATE) -> float:
    """EBU R128 integrated loudness (gated, so silence between lines does not count)."""
    arr = np.asarray(samples, dtype=np.float32)
    channels = 1 if arr.ndim == 1 else arr.shape[1]
    proc = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-f", "f32le", "-ar", str(rate), "-ac",
                           str(channels), "-i", "pipe:0", "-af", "ebur128", "-f", "null", "-"],
                          input=arr.tobytes(), capture_output=True)
    found = _LUFS.findall(proc.stderr.decode(errors="replace"))
    if not found or found[-1] == "-inf":
        return float("-inf")
    value = float(found[-1])
    # EBU R128 gates at -70 LUFS: a reading at the gate means nothing measurable (silence), and
    # normalising it would apply +54 dB to the noise floor.
    return value if value > -69.9 else float("-inf")


def normalize_loudness(samples: np.ndarray, target_lufs: float = -16.0, rate: int = MIX_RATE,
                       ceiling: float = 0.97) -> tuple[np.ndarray, float]:
    """Linear gain to `target_lufs`, then a soft ceiling for rare peaks. Returns (audio, measured LUFS after)."""
    current = integrated_lufs(samples, rate)
    if not np.isfinite(current):
        return samples, current
    out = samples * (10 ** ((target_lufs - current) / 20))
    peak = float(np.max(np.abs(out))) if out.size else 0.0
    if peak > ceiling:
        # tanh above the knee only: the body of the speech keeps its exact gain.
        knee = ceiling * 0.8
        over = np.abs(out) > knee
        span = ceiling - knee
        out[over] = np.sign(out[over]) * (knee + span * np.tanh((np.abs(out[over]) - knee) / span))
    return out, integrated_lufs(out, rate)
