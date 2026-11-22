"""ffmpeg-based audio plumbing: decode, silence detection, chunk planning, concatenation, encoding."""
from __future__ import annotations

import io
import re
import subprocess
import tempfile
import wave
from pathlib import Path

from .errors import ToolFailure


def run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
    except FileNotFoundError:
        raise ToolFailure("ffmpeg_missing", f"'{cmd[0]}' is not installed.",
                          hint="Install ffmpeg (apt install ffmpeg / brew install ffmpeg).")
    except subprocess.TimeoutExpired:
        raise ToolFailure("media_timeout", "Media processing timed out.", retryable=True)
    return p


def probe_duration(path: str | Path) -> float:
    p = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)])
    try:
        return float(p.stdout.strip())
    except ValueError:
        raise ToolFailure("media_decode_error", f"Could not read duration of '{path}'. Is it an audio/video file?",
                          hint="Supported: anything ffmpeg can decode (wav, mp3, m4a, flac, ogg, mp4, mov, mkv, webm).")


def has_audio_stream(path: str | Path) -> bool:
    p = run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
             "-of", "csv=p=0", str(path)])
    if p.returncode != 0:
        raise ToolFailure("media_decode_error", f"'{path}' is not a readable audio/video file.",
                          hint="Supported: anything ffmpeg can decode (wav, mp3, m4a, flac, ogg, mp4, mov, mkv, webm).")
    return bool(p.stdout.strip())


def decode_to_wav(src: str | Path, dst: str | Path, rate: int = 16000,
                  start: float | None = None, end: float | None = None) -> None:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-t", f"{max(0.0, end - (start or 0.0)):.3f}"]
    cmd += ["-i", str(src), "-vn", "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", str(dst)]
    p = run(cmd)
    if p.returncode != 0:
        raise ToolFailure("media_decode_error", f"ffmpeg could not decode '{src}': {p.stderr.strip()[-300:]}")


def detect_silences(wav_path: str | Path, noise_db: int = -35, min_len: float = 0.35) -> list[tuple[float, float]]:
    p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(wav_path), "-af",
             f"silencedetect=noise={noise_db}dB:d={min_len}", "-f", "null", "-"])
    out: list[tuple[float, float]] = []
    start: float | None = None
    for line in p.stderr.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            start = max(0.0, float(m.group(1)))
            continue
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            out.append((start, float(m.group(1))))
            start = None
    if start is not None:
        out.append((start, probe_duration(wav_path)))
    return out


def plan_chunks(duration: float, silences: list[tuple[float, float]], max_len: float = 28.0,
                pad: float = 0.2, max_gap: float = 3.0) -> list[tuple[float, float]]:
    """Plan ASR windows over the *speech* only (pure function, unit-tested).

    - speech = everything that is not a detected silence, padded a little so word edges survive;
    - consecutive speech stretches are packed into windows of at most max_len seconds, so cuts fall
      inside silences, never inside words;
    - long silences (> max_gap) always start a new window and are never sent (billing is per second);
    - a single stretch longer than max_len (no pauses at all) is cut into equal parts.
    """
    if duration <= 0.05:
        return []
    speech: list[tuple[float, float]] = []
    cur = 0.0
    for s_, e_ in sorted(silences):
        s_, e_ = max(0.0, s_), min(duration, e_)
        if s_ - cur > 0.15:
            speech.append((cur, s_))
        cur = max(cur, e_)
    if duration - cur > 0.15:
        speech.append((cur, duration))
    speech = [(max(0.0, a - pad), min(duration, b + pad)) for a, b in speech]

    pieces: list[tuple[float, float]] = []
    for a, b in speech:  # break up uninterrupted stretches that exceed the window
        n = int(-(-(b - a) // max_len))  # ceil
        if n <= 1:
            pieces.append((a, b))
        else:
            step = (b - a) / n
            pieces += [(a + i * step, a + (i + 1) * step) for i in range(n)]

    chunks: list[tuple[float, float]] = []
    for a, b in pieces:
        if chunks:
            ca, cb = chunks[-1]
            a = max(a, cb)  # padding of neighbours may overlap
            if a - cb <= max_gap and b - ca <= max_len:
                chunks[-1] = (ca, b)
                continue
        chunks.append((a, b))
    return [(round(a, 3), round(b, 3)) for a, b in chunks if b - a > 0.05]


def silence_fraction(start: float, end: float, silences: list[tuple[float, float]]) -> float:
    total = max(end - start, 1e-9)
    covered = sum(max(0.0, min(e, end) - max(s, start)) for s, e in silences)
    return covered / total


def read_wav(path: str | Path) -> tuple[tuple[int, int, int], bytes]:
    with wave.open(str(path), "rb") as w:
        return (w.getnchannels(), w.getsampwidth(), w.getframerate()), w.readframes(w.getnframes())


def slice_wav(params: tuple[int, int, int], frames: bytes, start: float, end: float) -> bytes:
    ch, sw, rate = params
    frame_size = ch * sw
    a = int(start * rate) * frame_size
    b = int(end * rate) * frame_size
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(sw)
        w.setframerate(rate)
        w.writeframes(frames[a:b])
    return buf.getvalue()


def concat_wavs(parts: list[bytes], gap_ms: int = 250) -> bytes:
    if not parts:
        raise ToolFailure("internal_error", "No audio to concatenate.")
    params = None
    frames = []
    for p in parts:
        with wave.open(io.BytesIO(p), "rb") as w:
            cur = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            if params is None:
                params = cur
            elif cur != params:
                raise ToolFailure("internal_error", f"Audio chunks differ in format: {params} vs {cur}")
            frames.append(w.readframes(w.getnframes()))
    ch, sw, rate = params  # type: ignore[misc]
    gap = b"\x00" * (int(rate * gap_ms / 1000) * ch * sw)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(sw)
        w.setframerate(rate)
        w.writeframes(gap.join(frames))
    return buf.getvalue()


def encode(wav_bytes: bytes, dst: Path, loudnorm: bool = True, comment: str | None = None) -> None:
    """Write wav_bytes to dst; format from extension (.wav .mp3 .ogg .flac). Optionally loudness-normalise."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    ext = dst.suffix.lower()
    if ext == ".wav" and not loudnorm and not comment:
        dst.write_bytes(wav_bytes)
        return
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(wav_bytes)
        tmp_path = tmp.name
    try:
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", tmp_path]
        if loudnorm:
            cmd += ["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"]
        if comment:
            cmd += ["-metadata", f"comment={comment}"]
        if ext == ".mp3":
            cmd += ["-c:a", "libmp3lame", "-q:a", "2"]
        elif ext == ".wav":
            cmd += ["-c:a", "pcm_s16le"]
        cmd.append(str(dst))
        p = run(cmd)
        if p.returncode != 0:
            raise ToolFailure("media_encode_error", f"ffmpeg could not write '{dst.name}': {p.stderr.strip()[-300:]}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)
