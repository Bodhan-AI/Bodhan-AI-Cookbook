"""Audio helpers.

Timebase rule: every audio file we extract is aligned to the CONTAINER timeline, so sample 0 is the
start of the file and a time t in any of our JSON files means "t seconds into the video". Streams can
start at different times (this matters for MP4/MOV/MKV/WebM edits, phone recordings, TS captures).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000


def _num(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def probe_media(path: str | Path) -> dict:
    """Stream start times and container duration.

    origin       = earliest stream start (t=0 of our timeline)
    audio_offset = how much later than the origin the audio starts
    video_offset = how much later than the origin the video starts
    duration     = container duration measured from the origin
    """
    if shutil.which("ffprobe") is None:
        raise RuntimeError("ffprobe not found on PATH. Install ffmpeg first.")
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=index,codec_type,codec_name,start_time,duration:format=start_time,duration",
                        "-of", "json", str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {r.stderr.strip()}")
    j = json.loads(r.stdout)
    streams, fmt = j.get("streams", []), j.get("format", {})
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    vs = _num(v.get("start_time")) if v else None
    as_ = _num(a.get("start_time")) if a else None
    starts = [x for x in (vs, as_) if x is not None]
    origin = min(starts) if starts else (_num(fmt.get("start_time")) or 0.0)
    duration = _num(fmt.get("duration"))
    if duration is None:
        duration = max((_num(s.get("duration")) or 0.0) for s in streams) if streams else 0.0
    return {
        "origin": origin,
        "video_start": vs, "audio_start": as_,
        "video_offset": None if vs is None else round(vs - origin, 6),
        "audio_offset": None if as_ is None else round(as_ - origin, 6),
        "duration": duration,
        "has_audio": a is not None, "has_video": v is not None,
        "video_codec": v.get("codec_name") if v else None,
    }


def extract_audio(video_path: str | Path, out_path: str | Path, sample_rate: int = SAMPLE_RATE,
                  channels: int = 1, duration: float | None = None) -> Path:
    """Extract the first audio track as PCM16 WAV, aligned to the container timeline.

    If the audio stream starts later than the file, silence is added in front (sample-exact);
    the result is padded/trimmed to `duration` (default: the container duration).
    Works for MP4, MOV, MKV and WebM. Raises FileNotFoundError / RuntimeError.
    """
    video_path, out_path = Path(video_path), Path(out_path)
    if not video_path.is_file():
        raise FileNotFoundError(f"Input video not found: {video_path}")
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found on PATH. Install ffmpeg first.")
    m = probe_media(video_path)
    if not m["has_audio"]:
        raise RuntimeError(f"No audio stream in {video_path}")
    total = duration if duration is not None else m["duration"]
    delay = int(round((m["audio_offset"] or 0.0) * sample_rate))

    # resample (filling any timestamp gaps), then restart timestamps at 0 so the delay and -t are exact
    filters = [f"aresample={sample_rate}:async=1", "asetpts=N/SR/TB"]
    if delay > 0:
        filters.append(f"adelay=delays={delay}S:all=1")
    filters.append("apad")  # endless silence; -t below cuts at the exact total length

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video_path),
           "-map", "0:a:0", "-vn", "-af", ",".join(filters), "-ac", str(channels), "-ar", str(sample_rate),
           "-t", f"{total:.6f}", "-c:a", "pcm_s16le", str(out_path)]
    log.info("extract_audio: %s -> %s (audio_offset=%.3fs, duration=%.3fs)", video_path.name, out_path,
             m["audio_offset"] or 0.0, total)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.strip()}")
    return out_path
