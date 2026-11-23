"""Thin ffmpeg/ffprobe wrapper.

All audio work (decode, resample, time-stretch, loudnorm, mux) is delegated to
ffmpeg so the Python dependency set stays compile-free. We resolve a system
ffmpeg first and fall back to the pip-installed static binary from imageio-ffmpeg,
so local dev works even without `brew install ffmpeg` (Docker/README use system ffmpeg).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from functools import lru_cache

import numpy as np
import soundfile as sf


class FFmpegError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def ffmpeg_bin() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as e:  # pragma: no cover - only hit with no ffmpeg at all
        raise FFmpegError(
            "ffmpeg not found. Install system ffmpeg or `pip install imageio-ffmpeg`."
        ) from e


@lru_cache(maxsize=1)
def ffprobe_bin() -> str | None:
    return shutil.which("ffprobe")


def run(cmd: list[str], *, capture: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-1500:]
        raise FFmpegError(f"command failed ({proc.returncode}): {' '.join(cmd[:4])} ...\n{tail}")
    return proc


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)")


def probe_duration(path: str) -> float:
    """Media duration in seconds. Uses ffprobe when present, else parses ffmpeg -i."""
    fp = ffprobe_bin()
    if fp:
        proc = run([fp, "-v", "error", "-show_entries", "format=duration",
                    "-of", "json", path])
        try:
            return float(json.loads(proc.stdout)["format"]["duration"])
        except Exception:
            pass
    # Fallback: ffmpeg prints Duration to stderr even though it "fails" with no output.
    proc = subprocess.run([ffmpeg_bin(), "-i", path, "-hide_banner"],
                          capture_output=True, text=True)
    m = _DURATION_RE.search(proc.stderr or "")
    if not m:
        raise FFmpegError(f"could not determine duration of {path}")
    h, mnt, sec = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(sec)


def extract_audio(video: str, out_wav: str, sr: int = 16000, mono: bool = True) -> None:
    """Decode a video/audio file to PCM WAV at the given sample rate."""
    run([ffmpeg_bin(), "-y", "-i", video, "-vn",
         "-ac", "1" if mono else "2", "-ar", str(sr),
         "-c:a", "pcm_s16le", out_wav])


def read_wav(path: str) -> tuple[np.ndarray, int]:
    """Return (float32 mono samples in [-1,1], sample_rate)."""
    data, sr = sf.read(path, dtype="float32", always_2d=False)
    if data.ndim == 2:
        data = data.mean(axis=1)
    return data, sr


def write_wav(path: str, samples: np.ndarray, sr: int) -> None:
    sf.write(path, np.asarray(samples, dtype=np.float32), sr, subtype="PCM_16")


def _atempo_chain(factor: float) -> str:
    """ffmpeg atempo accepts 0.5–2.0 per filter; chain for values outside that.

    Our fit logic clamps to [0.85, 1.2], so a single stage always suffices here,
    but the chain keeps the helper correct for any factor.
    """
    factor = max(0.25, min(4.0, factor))
    stages = []
    f = factor
    while f > 2.0:
        stages.append(2.0)
        f /= 2.0
    while f < 0.5:
        stages.append(0.5)
        f /= 0.5
    stages.append(f)
    return ",".join(f"atempo={s:.6f}" for s in stages)


def time_stretch(in_wav: str, out_wav: str, factor: float) -> None:
    """Speed audio by `factor` (``>1`` = faster/shorter) preserving pitch via atempo."""
    if abs(factor - 1.0) < 1e-3:
        shutil.copyfile(in_wav, out_wav)
        return
    run([ffmpeg_bin(), "-y", "-i", in_wav, "-filter:a", _atempo_chain(factor), out_wav])


def loudnorm(in_wav: str, out_wav: str, target_lufs: float = -16.0) -> dict:
    """Normalize to EBU R128 target and return measured loudness stats.

    Single-pass loudnorm with print_format=json; the printed measurements let the
    QA stage report output LUFS without a scipy/pyloudnorm dependency.
    """
    proc = subprocess.run(
        [ffmpeg_bin(), "-y", "-i", in_wav,
         "-filter:a", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11:print_format=json",
         out_wav],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise FFmpegError((proc.stderr or "")[-1500:])
    stats: dict = {}
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr or "", re.DOTALL)
    if m:
        try:
            stats = json.loads(m.group(0))
        except Exception:
            stats = {}
    return stats


def burn_subtitles(video: str, srt: str, out_mp4: str, font: str = "", font_size: int = 16) -> None:
    """Hardcode subtitles into the video frames (always visible, any player).

    Re-encodes video (libass), copies audio. Needs a font that covers the target
    script; `font` names it (per-script defaults are chosen by the caller).
    """
    style = f"FontSize={font_size},OutlineColour=&H80000000,BorderStyle=3,Outline=1,Shadow=0,MarginV=25"
    if font:
        style = f"FontName={font}," + style
    srt_esc = srt.replace("\\", "\\\\").replace(":", "\\:")
    run([ffmpeg_bin(), "-y", "-i", video,
         "-vf", f"subtitles={srt_esc}:force_style='{style}'",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy", out_mp4])


def mux(video: str, audio_wav: str, out_mp4: str,
        subtitles: str | None = None, sub_lang: str | None = None) -> None:
    """Replace the video's audio with `audio_wav`; optionally embed a soft subtitle track.

    Subtitles are added as a toggleable `mov_text` stream so players (QuickTime/VLC)
    can show them, while the sidecar .srt/.vtt files are still written separately.
    """
    cmd = [ffmpeg_bin(), "-y", "-i", video, "-i", audio_wav]
    if subtitles:
        cmd += ["-i", subtitles]
    cmd += ["-map", "0:v:0", "-map", "1:a:0"]
    if subtitles:
        cmd += ["-map", "2:0", "-c:s", "mov_text"]
        if sub_lang:
            cmd += ["-metadata:s:s:0", f"language={sub_lang}"]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", out_mp4]
    run(cmd)
