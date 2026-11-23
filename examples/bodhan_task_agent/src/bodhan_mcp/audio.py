"""Audio plumbing: probing, decoding, silence-aware chunking, concatenation, encoding.

Indic-Transcribe accepts about 30 seconds per request, so long audio is cut into
utterance-sized segments *at pauses*, never mid-word when a pause exists. Pure
silence is never sent to the model, so a silent stretch cannot be "transcribed"
into hallucinated text.
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

from . import errors

TRANSCRIBE_SR = 16_000
MAX_WINDOW_S = 28.0  # stay safely under the ~30 s model window
TARGET_SEGMENT_S = 15.0  # group pauses into segments of about this length (good subtitle granularity)
FRAME_S = 0.03
MIN_PAUSE_S = 0.3
PAD_S = 0.12

AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wma", ".amr", ".webm"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".3gp"}


@dataclass
class Segment:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def have_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _require_ffmpeg(purpose: str) -> None:
    if not have_ffmpeg():
        raise errors.BodhanError(
            errors.MEDIA_TOOL_MISSING,
            f"ffmpeg/ffprobe are required to {purpose} but were not found on PATH.",
            hint="Install ffmpeg (apt install ffmpeg / brew install ffmpeg) or use the Docker image.",
        )


def probe(path: Path) -> dict:
    """Container-level facts about a media file (duration, streams). Works without ffmpeg for PCM WAV."""
    if not have_ffmpeg():
        if path.suffix.lower() == ".wav":
            with wave.open(str(path), "rb") as wf:
                return {"duration_s": wf.getnframes() / wf.getframerate(), "has_audio": True, "has_video": False,
                        "sample_rate": wf.getframerate(), "channels": wf.getnchannels(), "format": "wav"}
        _require_ffmpeg("inspect media")
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    if proc.returncode != 0:
        raise errors.BodhanError(
            errors.UNSUPPORTED_MEDIA, f"Could not read {path.name} as media: {proc.stderr.strip()[:200]}",
            hint="Pass a valid audio or video file (wav, mp3, m4a, ogg, flac, mp4, mov, mkv, webm).",
        )
    info = json.loads(proc.stdout or "{}")
    streams = info.get("streams", [])
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    video = next((s for s in streams if s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1), None)
    dur = info.get("format", {}).get("duration") or (audio or {}).get("duration") or 0
    return {
        "duration_s": round(float(dur), 3),
        "has_audio": audio is not None,
        "has_video": video is not None,
        "sample_rate": int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        "channels": audio.get("channels") if audio else None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "format": info.get("format", {}).get("format_name"),
    }


def _resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out or x.size == 0:
        return x
    n_out = round(x.size * sr_out / sr_in)
    t_in = np.linspace(0.0, 1.0, num=x.size, endpoint=False)
    t_out = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(t_out, t_in, x.astype(np.float64)).astype(np.int16)


def wav_bytes_to_pcm(data: bytes) -> tuple[np.ndarray, int]:
    """Decode PCM16 WAV bytes into mono int16 samples."""
    with wave.open(io.BytesIO(data), "rb") as wf:
        sr, ch, width = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if width != 2:
        raise errors.BodhanError(errors.UNSUPPORTED_MEDIA, f"Expected 16-bit PCM WAV, got {8 * width}-bit.")
    x = np.frombuffer(raw, dtype=np.int16)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1).astype(np.int16)
    return x, sr


def pcm_to_wav_bytes(x: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(np.asarray(x, dtype=np.int16).tobytes())
    return buf.getvalue()


def decode_to_pcm(path: Path, sr: int = TRANSCRIBE_SR) -> np.ndarray:
    """Decode any audio/video file's first audio stream into mono int16 at ``sr``."""
    if not have_ffmpeg():
        if path.suffix.lower() == ".wav":
            x, sr_in = wav_bytes_to_pcm(path.read_bytes())
            return _resample(x, sr_in, sr)
        _require_ffmpeg("decode audio")
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", str(sr),
         "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"],
        capture_output=True, timeout=600, check=False,
    )
    if proc.returncode != 0:
        msg = proc.stderr.decode(errors="replace").strip()[:200]
        raise errors.BodhanError(
            errors.UNSUPPORTED_MEDIA, f"Could not decode audio from {path.name}: {msg or 'no audio stream'}",
            hint="The file may have no audio track or be corrupt.",
        )
    return np.frombuffer(proc.stdout, dtype=np.int16).copy()


def frame_db(x: np.ndarray, sr: int, frame_s: float = FRAME_S) -> np.ndarray:
    n = max(1, int(sr * frame_s))
    usable = (x.size // n) * n
    if usable == 0:
        return np.array([-120.0])
    frames = x[:usable].astype(np.float64).reshape(-1, n) / 32768.0
    rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-12)


def speech_mask(db: np.ndarray) -> np.ndarray:
    """Adaptive energy VAD: a frame is speech if it is well above the noise floor."""
    if db.size == 0:
        return np.zeros(0, dtype=bool)
    floor = float(np.percentile(db, 10))
    peak = float(np.percentile(db, 95))
    if peak < -55:  # the whole file is near-silent
        return np.zeros(db.size, dtype=bool)
    thresh = max(floor + 0.3 * (peak - floor), floor + 6.0, -55.0)
    return db > thresh


def segment_speech(
    x: np.ndarray,
    sr: int,
    *,
    max_s: float = MAX_WINDOW_S,
    target_s: float = TARGET_SEGMENT_S,
    min_pause_s: float = MIN_PAUSE_S,
) -> list[Segment]:
    """Split audio into speech segments no longer than ``max_s``, cutting at pauses.

    1. Mark speech frames with an adaptive energy threshold.
    2. Close gaps shorter than ``min_pause_s`` (a breath is not a pause).
    3. Greedily group speech regions into segments up to ``target_s``; never exceed ``max_s``.
    4. A single region longer than ``max_s`` is cut at its quietest frame in the last 40% of the window.
    Silence-only stretches produce no segment.
    """
    db = frame_db(x, sr)
    mask = speech_mask(db)
    total_s = x.size / sr
    if not mask.any():
        return []
    # Speech regions as [start_frame, end_frame)
    regions: list[list[int]] = []
    i = 0
    n = mask.size
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            regions.append([i, j])
            i = j
        else:
            i += 1
    gap_frames = int(min_pause_s / FRAME_S)
    merged: list[list[int]] = [regions[0]]
    for r in regions[1:]:
        if r[0] - merged[-1][1] < gap_frames:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    # Drop clicks shorter than 120 ms
    merged = [r for r in merged if (r[1] - r[0]) * FRAME_S >= 0.12]

    # Split any region longer than max_s at its quietest point.
    max_frames = int(max_s / FRAME_S)
    split: list[list[int]] = []
    for s, e in merged:
        while e - s > max_frames:
            lo = s + int(max_frames * 0.6)
            hi = s + max_frames
            cut = lo + int(np.argmin(db[lo:hi]))
            split.append([s, cut])
            s = cut
        split.append([s, e])

    # Group into segments up to target_s (cut points fall in the pauses between regions).
    target_frames = int(target_s / FRAME_S)
    groups: list[list[int]] = []
    for s, e in split:
        if groups and (e - groups[-1][0]) <= target_frames and (e - groups[-1][0]) <= max_frames:
            groups[-1][1] = e
        else:
            groups.append([s, e])

    out: list[Segment] = []
    for s, e in groups:
        start = max(0.0, s * FRAME_S - PAD_S)
        end = min(total_s, e * FRAME_S + PAD_S)
        if out and start < out[-1].end:
            start = out[-1].end
        if end - start > max_s + 2 * PAD_S:
            end = start + max_s + 2 * PAD_S
        out.append(Segment(round(start, 3), round(end, 3)))
    return out


def slice_pcm(x: np.ndarray, sr: int, seg: Segment) -> np.ndarray:
    return x[int(seg.start * sr): int(seg.end * sr)]


def concat(chunks: list[np.ndarray], sr: int, gap_s: float = 0.18) -> np.ndarray:
    gap = np.zeros(int(sr * gap_s), dtype=np.int16)
    parts: list[np.ndarray] = []
    for i, c in enumerate(chunks):
        if i:
            parts.append(gap)
        parts.append(np.asarray(c, dtype=np.int16))
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16)


def stats(x: np.ndarray, sr: int) -> dict:
    if x.size == 0:
        return {"duration_s": 0.0, "peak_dbfs": None, "silence_ratio": 1.0, "clipping_ratio": 0.0}
    f = x.astype(np.float64) / 32768.0
    db = frame_db(x, sr)
    return {
        "duration_s": round(x.size / sr, 3),
        "peak_dbfs": round(float(20 * np.log10(np.max(np.abs(f)) + 1e-12)), 2),
        "silence_ratio": round(float(np.mean(db < -45)), 3),
        "clipping_ratio": round(float(np.mean(np.abs(x.astype(np.int32)) >= 32700)), 5),
    }


AI_LABEL = "AI-generated speech (Bodhan Indic-Speak). Not a human recording."


def encode(x: np.ndarray, sr: int, out_path: Path, *, title: str | None = None, normalize: bool = True) -> Path:
    """Write mono PCM to WAV or MP3 (by extension), loudness-normalised to -16 LUFS, labelled AI-generated."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = out_path.suffix.lower().lstrip(".")
    if not have_ffmpeg():
        if fmt != "wav":
            _require_ffmpeg(f"encode {fmt}")
        out_path.write_bytes(pcm_to_wav_bytes(x, sr))
        return out_path
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "s16le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0"]
    if normalize and x.size > sr * 0.5:  # loudnorm needs a little audio to measure
        cmd += ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
    cmd += ["-ar", str(sr), "-metadata", f"comment={AI_LABEL}", "-metadata", "artist=Bodhan Indic-Speak (AI-generated)"]
    if title:
        cmd += ["-metadata", f"title={title}"]
    if fmt == "mp3":
        cmd += ["-codec:a", "libmp3lame", "-q:a", "3"]
    elif fmt == "wav":
        cmd += ["-codec:a", "pcm_s16le"]
    else:
        raise errors.BodhanError(errors.INVALID_INPUT, f"Unsupported output audio format: {fmt!r}", hint="Use mp3 or wav.")
    cmd.append(str(out_path))
    proc = subprocess.run(cmd, input=np.asarray(x, dtype=np.int16).tobytes(), capture_output=True, timeout=600, check=False)
    if proc.returncode != 0:
        raise errors.BodhanError(errors.UPSTREAM_ERROR, f"ffmpeg failed to encode audio: {proc.stderr.decode()[:200]}")
    return out_path
