"""Muxing the dubbed audio and subtitles into the video.

The video stream is copied, never re-encoded, so the output looks exactly like
the input and the mux takes seconds rather than minutes. If the source codec
cannot live in an MP4 (VP8/VP9 from a WebM, for example), the copy attempt fails
and the file is transcoded instead — slower, but it means any of the four
supported containers works without the caller having to know.

Duration is forced to the input duration with ``-t``. Without it, a dub track a
few milliseconds long changes the container duration, and the brief asks for
±100 ms.

Subtitles are carried inside the MP4 as a real subtitle track (``mov_text``).
The sidecar ``.srt`` is still written, because the brief asks for a subtitle
file and a sidecar is portable. Embedding costs nothing — no re-encode — and
means the file has captions when opened in a player that reads them.

``burn_subtitles=True`` renders the cues into the picture instead, for players
and recordings where a selectable track is not enough. That needs libass, which
not every ffmpeg build has; the error says so explicitly rather than producing a
video with invisible captions.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from . import media
from .errors import MediaError

AUDIO_BITRATE = "192k"
BURN_STYLE = "FontSize=20,Outline=1,Shadow=0,MarginV=24"


def mux(
    video_path: Path,
    audio_path: Path,
    out_path: Path,
    *,
    duration_s: float,
    subtitle_path: Path | None = None,
    subtitle_language: str | None = None,
    burn_subtitles: bool = False,
) -> dict[str, Any]:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if burn_subtitles:
        if subtitle_path is None:
            raise MediaError("--burn-subtitles needs a subtitle file, and none was written")
        if not media.has_filter("subtitles"):
            raise MediaError(
                "this ffmpeg build has no libass, so subtitles cannot be burned in. "
                "Use the Dockerfile (Debian ffmpeg includes libass), install an ffmpeg "
                "built with --enable-libass, or open the sidecar .srt in your player."
            )
        video_args = [
            "-vf",
            f"subtitles='{_escape(str(subtitle_path))}':force_style='{BURN_STYLE}'",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
        ]
        mode = "burned"
    else:
        video_args = ["-c:v", "copy"]
        mode = "embedded" if subtitle_path is not None else "none"

    argv = _argv(
        video_path,
        audio_path,
        out_path,
        duration_s,
        video_args,
        subtitle_path=None if burn_subtitles else subtitle_path,
        subtitle_language=subtitle_language,
    )

    try:
        _run(argv)
        video_mode = "transcode" if burn_subtitles else "copy"
    except MediaError:
        if burn_subtitles:
            raise
        # Container or codec not MP4-compatible; re-encode the video.
        encoded = _argv(
            video_path,
            audio_path,
            out_path,
            duration_s,
            ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"],
            subtitle_path=subtitle_path,
            subtitle_language=subtitle_language,
        )
        _run(encoded)
        video_mode = "transcode"

    return {"video": video_mode, "subtitles": mode, "out": str(out_path)}


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


def has_subtitle_track(path: Path | str) -> bool:
    """True when the file carries a subtitle stream players can select."""
    import json

    result = subprocess.run(
        [
            media.find_binary("ffprobe"),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout.decode("utf-8", "replace"))
    return any(stream.get("codec_type") == "subtitle" for stream in payload.get("streams", []))


def _argv(
    video_path: Path,
    audio_path: Path,
    out_path: Path,
    duration_s: float,
    video_args: list[str],
    *,
    subtitle_path: Path | None,
    subtitle_language: str | None,
) -> list[str]:
    argv = [
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
    ]
    if subtitle_path is not None:
        argv += ["-i", str(subtitle_path)]

    argv += ["-map", "0:v:0", "-map", "1:a:0", *video_args]
    argv += [
        "-c:a",
        "aac",
        "-b:a",
        AUDIO_BITRATE,
        "-ac",
        "1",
        "-ar",
        str(media.TTS_SAMPLE_RATE),
    ]
    if subtitle_path is not None:
        argv += [
            "-map",
            "2:0",
            "-c:s",
            "mov_text",
            "-metadata:s:s:0",
            f"language={subtitle_language or 'und'}",
        ]
    argv += ["-t", f"{duration_s:.3f}", "-movflags", "+faststart", str(out_path)]
    return argv


def _escape(path: str) -> str:
    """Escape a path for an ffmpeg filter argument."""
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _run(argv: list[str]) -> None:
    try:
        subprocess.run(argv, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or b"").decode("utf-8", "replace").strip()[-400:]
        raise MediaError(f"mux failed: {tail}") from exc
