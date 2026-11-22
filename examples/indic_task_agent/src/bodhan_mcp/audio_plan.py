"""Plan how to cut long audio into windows the transcription API accepts (<= 30 s).

Pure functions (parse_silencedetect, plan_windows, *_cmd) are unit-tested; the thin
subprocess wrappers at the bottom need ffmpeg/ffprobe on PATH.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

API_MAX_SECONDS = 30.0  # Indic-Transcribe rejects longer audio

_SIL_START = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SIL_END = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


def parse_silencedetect(stderr: str, total_duration: float | None = None) -> list[tuple[float, float]]:
    """Parse ffmpeg's silencedetect log into (start, end) pairs in seconds."""
    silences: list[tuple[float, float]] = []
    open_start: float | None = None
    for line in stderr.splitlines():
        m_start = _SIL_START.search(line)
        if m_start:
            open_start = max(0.0, float(m_start.group(1)))
        m_end = _SIL_END.search(line)
        if m_end and open_start is not None:
            silences.append((open_start, max(open_start, float(m_end.group(1)))))
            open_start = None
    if open_start is not None and total_duration is not None:
        silences.append((open_start, max(open_start, total_duration)))
    return silences


def plan_windows(
    duration: float,
    silences: list[tuple[float, float]] | None = None,
    max_len: float = 25.0,
    min_len: float = 2.0,
    target_len: float | None = None,
) -> list[tuple[float, float]]:
    """Cut [0, duration] into contiguous windows no longer than `max_len` seconds.

    Cuts land in the middle of a silence when one is available between `min_len` and
    `max_len` after the window start; the one closest to `target_len` wins (default:
    as long as allowed). Use a small target_len (e.g. 8) for finer subtitle cues.
    A tiny tail is merged into the previous window while the result stays under the API cap.
    """
    if duration <= 0:
        return []
    if not 0 < min_len < max_len <= API_MAX_SECONDS:
        raise ValueError(f"need 0 < min_len < max_len <= {API_MAX_SECONDS}")
    target = min(max(target_len or max_len, min_len), max_len)
    midpoints = sorted((s + e) / 2 for s, e in (silences or []))
    # With a short target (subtitle mode) keep cutting until the remainder is also short.
    keep_cutting_above = max_len if target_len is None else min(max_len, 1.5 * target)

    windows: list[tuple[float, float]] = []
    pos = 0.0
    while duration - pos > keep_cutting_above + 1e-9:
        lo, hi = pos + min_len, pos + max_len
        candidates = [m for m in midpoints if lo <= m <= hi]
        if candidates:
            cut = min(candidates, key=lambda m: abs(m - (pos + target)))
        else:
            cut = pos + target
        windows.append((pos, cut))
        pos = cut
    windows.append((pos, duration))

    if len(windows) > 1 and windows[-1][1] - windows[-1][0] < min_len:
        merged_start = windows[-2][0]
        if duration - merged_start <= API_MAX_SECONDS - 0.5:
            windows = windows[:-2] + [(merged_start, duration)]
    return [(round(s, 3), round(e, 3)) for s, e in windows]


# ---- ffmpeg command builders (pure) ----

def probe_duration_cmd(path: str | Path) -> list[str]:
    return ["ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path)]


def silencedetect_cmd(path: str | Path, noise_db: int = -35, min_silence: float = 0.4) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
            "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}", "-f", "null", "-"]


def extract_window_cmd(src: str | Path, dst: str | Path, start: float, end: float,
                       sample_rate: int = 16000) -> list[str]:
    """Cut [start, end] from any audio/video file into 16 kHz mono PCM WAV."""
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{end - start:.3f}",
            "-vn", "-ac", "1", "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(dst)]


# ---- thin subprocess wrappers (need ffmpeg on PATH) ----

def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except FileNotFoundError as exc:
        raise RuntimeError(f"'{cmd[0]}' was not found on PATH. Install ffmpeg and retry.") from exc


def probe_duration(path: str | Path) -> float:
    result = _run(probe_duration_cmd(path))
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"Could not read the duration of {path}: {result.stderr.strip()[:300]}") from exc


def detect_silences(path: str | Path, duration: float | None = None, **kwargs) -> list[tuple[float, float]]:
    result = _run(silencedetect_cmd(path, **kwargs))
    return parse_silencedetect(result.stderr, duration)


def extract_window(src: str | Path, dst: str | Path, start: float, end: float) -> Path:
    result = _run(extract_window_cmd(src, dst, start, end))
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed to cut {start:.1f}-{end:.1f}s: {result.stderr.strip()[:300]}")
    return Path(dst)
