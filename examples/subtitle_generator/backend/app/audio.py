"""Everything that touches FFmpeg (finding it, inspecting a video, extracting and cutting audio), and planning the transcription pieces."""

import os
import re
import subprocess
from collections.abc import Callable

from .config import settings

# A pause at least this long (seconds) usually ends a sentence: start a new transcription request there...
LONG_PAUSE = 0.7
# ...once the current request already holds this many seconds of audio (fewer, larger requests otherwise)
MIN_PIECE = 6.0
# Where sound runs on longer than a piece can hold, the cut is placed within this many seconds before the limit
CUT_SEARCH = 6.0


class MediaError(Exception):
    """A problem with the video or its link (no audio, too long, unreadable, can't be downloaded)."""


def find_ffmpeg() -> str | None:
    """
    FFmpeg comes from the imageio-ffmpeg package inside the virtual environment, so every
    machine runs the same build and nothing is installed system-wide. FFMPEG_PATH overrides it.
    """
    if settings.ffmpeg_path:
        return settings.ffmpeg_path if os.path.isfile(settings.ffmpeg_path) else None
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def ffmpeg() -> str:
    path = find_ffmpeg()
    if not path:
        raise RuntimeError("FFmpeg not found. Run `pip install -r requirements.txt`, or fix FFMPEG_PATH in .env.")
    return path


def probe(video_path: str) -> tuple[float | None, bool | None]:
    """
    Return (duration in seconds, has an audio track), read from `ffmpeg -i` output.
    Either value is None when FFmpeg can't tell.
    """
    result = subprocess.run(
        [ffmpeg(), "-hide_banner", "-nostdin", "-i", video_path],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    # With no output file FFmpeg exits non-zero, but still describes the input on stderr
    info = result.stderr
    duration = None
    matched = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", info)
    if matched:
        hours, minutes, seconds = matched.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    has_audio = bool(re.search(r"Stream #.*: Audio:", info)) if "Stream #" in info else None
    return duration, has_audio


def extract_audio(video_path: str, wav_path: str) -> None:
    """Write the video's audio as 16 kHz mono 16-bit WAV, the format indic-transcribe expects."""
    _run(
        [ffmpeg(), "-nostdin", "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", wav_path],
        timeout=settings.max_video_seconds + 60,
        error="Could not read the audio track. Is the file a valid video?",
    )


class Piece:
    """One request's worth of audio: [start, end] in the full track, and the stretches of sound inside it."""

    def __init__(self, start: float, end: float):
        self.start, self.end = start, end
        self.speech = [(start, end)]

    def __repr__(self):
        return f"Piece({self.start:.2f}-{self.end:.2f}, speech={self.speech})"


def plan_pieces(speech: list[tuple[float, float]], max_seconds: float = 24.0, cut_at: Callable[[float, float], float] | None = None) -> list[Piece]:
    """
    Group stretches of sound into pieces, one transcription request each, cutting at pauses.

    The API returns one unpunctuated block of text per request, so text can't be split between the
    stretches of one piece reliably. A clear pause (LONG_PAUSE) therefore starts a new piece once the
    current one has MIN_PIECE seconds; shorter pauses are kept inside a piece. No piece exceeds
    max_seconds (indic-transcribe accepts ~30 s). A stretch longer than that is cut where cut_at(earliest, latest)
    says (the quietest moment in the last CUT_SEARCH seconds), or at max_seconds without it.
    """
    max_seconds = min(28.0, max(5.0, max_seconds))
    pieces: list[Piece] = []
    for start, end in speech:
        while end - start > max_seconds:  # no pause to cut at
            latest = start + max_seconds
            # Search only after the piece's first second, so every cut moves forward (also with short CHUNK_SECONDS)
            cut = cut_at(max(start + 1.0, latest - CUT_SEARCH), latest) if cut_at else latest
            if not start < cut <= latest:
                cut = latest
            pieces.append(Piece(start, cut))
            start = cut
        if pieces:
            last = pieces[-1]
            pause = start - last.end
            fits = end - last.start <= max_seconds
            long_enough = last.end - last.start >= MIN_PIECE
            if fits and pause >= 0 and not (pause >= LONG_PAUSE and long_enough):
                last.end = end
                last.speech.append((start, end))
                continue
        pieces.append(Piece(start, end))
    return pieces


def cut_audio(wav_path: str, start: float, end: float, out_path: str) -> None:
    _run(
        [ffmpeg(), "-nostdin", "-y", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", wav_path, "-c:a", "pcm_s16le", out_path],
        timeout=60,
        error=f"Could not cut the audio at {start:.1f}s.",
    )


def _run(cmd: list[str], timeout: float, error: str) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode != 0:
        raise MediaError(error)
