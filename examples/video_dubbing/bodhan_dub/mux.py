"""Muxing the dubbed audio back into the video.

The video stream is copied, never re-encoded, so the output looks exactly like
the input and the mux takes seconds rather than minutes. If the source codec
cannot live in an MP4 (VP8/VP9 from a WebM, for example), the copy attempt fails
and the file is transcoded instead — slower, but it means any of the four
supported containers works without the caller having to know.

Duration is forced to the input duration with ``-t``. Without it, a dub track a
few milliseconds long changes the container duration, and the brief asks for
±100 ms.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import media
from .errors import MediaError

AUDIO_BITRATE = "192k"


def mux(
    video_path: Path,
    audio_path: Path,
    out_path: Path,
    *,
    duration_s: float,
) -> dict[str, Any]:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        _run(_argv(video_path, audio_path, out_path, duration_s, ["-c:v", "copy"]))
        mode = "copy"
    except MediaError:
        # Container or codec not MP4-compatible; re-encode the video.
        _run(
            _argv(
                video_path,
                audio_path,
                out_path,
                duration_s,
                ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"],
            )
        )
        mode = "transcode"

    return {"video": mode, "out": str(out_path)}


def _argv(
    video_path: Path,
    audio_path: Path,
    out_path: Path,
    duration_s: float,
    video_args: list[str],
) -> list[str]:
    return [
        media.find_binary("ffmpeg"),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        *video_args,
        "-c:a",
        "aac",
        "-b:a",
        AUDIO_BITRATE,
        "-ac",
        "1",
        "-ar",
        str(media.TTS_SAMPLE_RATE),
        "-t",
        f"{duration_s:.3f}",
        "-movflags",
        "+faststart",
        str(out_path),
    ]


def verify_duration(
    path: Path | str,
    expected_s: float,
    *,
    tolerance_s: float = 0.1,
) -> dict[str, Any]:
    info = media.probe(path)
    delta = info.duration_s - expected_s
    return {
        "duration_s": round(info.duration_s, 3),
        "expected_s": round(expected_s, 3),
        "delta_ms": round(delta * 1000.0, 1),
        "within_tolerance": abs(delta) <= tolerance_s,
        "tolerance_ms": int(tolerance_s * 1000),
    }


def _run(argv: list[str]) -> None:
    import subprocess

    try:
        subprocess.run(argv, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or b"").decode("utf-8", "replace").strip()[-400:]
        raise MediaError(f"mux failed: {tail}") from exc
