"""Local format conversion only: no OCR, ASR, translation or TTS models here."""

from __future__ import annotations
import unicodedata
import json
import re
import subprocess
import wave
from pathlib import Path

from .runtime import ToolError, digest


def command(args: list[str], timeout: float = 45) -> bytes:
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise ToolError("system_dependency", f"Install system dependency {args[0]}.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError("media_timeout", "Media conversion timed out.") from exc
    if proc.returncode:
        raise ToolError("invalid_media", f"{args[0]} could not decode this media file.")
    return proc.stdout


def duration(path: Path, timeout: float = 45) -> float:
    value = json.loads(
        command(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            timeout,
        )
    )
    try:
        seconds = float(value["format"]["duration"])
    except (KeyError, ValueError) as exc:
        raise ToolError("invalid_media", "Cannot measure audio duration.") from exc
    if not 0 < seconds <= 7200:
        raise ToolError("media_limit", "Audio must be nonempty and at most two hours.")
    return seconds


def audio_chunks(path: Path, work: Path, timeout: float = 45) -> list[tuple[Path, float, float]]:
    duration(path, timeout)
    folder = work / (digest(path.read_bytes())[:16] + "_audio")
    folder.mkdir(exist_ok=True)
    # Decode once, then split at exact PCM sample boundaries, not packet boundaries.
    normalized_audio = folder / "normalized.wav"
    command(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(normalized_audio),
        ],
        timeout,
    )
    segments = []
    with wave.open(str(normalized_audio), "rb") as source:
        frame_rate = source.getframerate()
        total_frames = source.getnframes()
        frames_per_chunk = 25 * frame_rate
        for index, start_frame in enumerate(range(0, total_frames, frames_per_chunk)):
            data = source.readframes(frames_per_chunk)
            part = folder / f"{index:05d}.wav"
            with wave.open(str(part), "wb") as dest:
                dest.setparams(source.getparams())
                dest.writeframes(data)
            end_frame = min(start_frame + frames_per_chunk, total_frames)
            segments.append((part, start_frame / frame_rate, end_frame / frame_rate))
    return segments


def text_chunks(text: str, limit: int = 1200) -> list[str]:
    """Split losslessly at Unicode character boundaries, preferably sentence ends."""
    if not text.strip():
        raise ToolError("empty_text", "Text is empty; supply a nonempty source document.")
    chunks: list[str] = []
    current = ""
    for char in text:
        if len((current + char).encode("utf-8")) > limit:
            boundary = max(
                current.rfind("\n"), current.rfind(". "), current.rfind("।"), current.rfind(" ")
            )
            if boundary > len(current) // 2:
                chunks.append(current[: boundary + 1])
                current = current[boundary + 1 :]
            else:
                chunks.append(current)
                current = ""
        current += char
    if current:
        chunks.append(current)
    return chunks


def script_language(text: str) -> str | None:
    """Only distinctive scripts identify a language; shared scripts remain unknown.

    Bengali script is shared with Assamese, so it cannot establish Bengali alone.
    Devanagari similarly cannot establish Hindi or Marathi without a user hint.
    """
    ranges = {
        "te": (0x0C00, 0x0C7F),
        "ta": (0x0B80, 0x0BFF),
        "kn": (0x0C80, 0x0CFF),
        "ml": (0x0D00, 0x0D7F),
        "gu": (0x0A80, 0x0AFF),
        "or": (0x0B00, 0x0B7F),
        "pa": (0x0A00, 0x0A7F),
    }
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return None
    scores = {
        lang: sum(lo <= ord(c) <= hi for c in letters) / len(letters)
        for lang, (lo, hi) in ranges.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] >= 0.65 else None


def normalized(text: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFC", text).casefold() if c.isalnum())


def similarity(left: str, right: str) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, normalized(left), normalized(right), autojunk=False).ratio()


def timestamp(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, rest = divmod(millis, 3600000)
    minutes, rest = divmod(rest, 60000)
    sec, ms = divmod(rest, 1000)
    return f"{hours:02}:{minutes:02}:{sec:02},{ms:03}"


def plain_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def english_script(value: str) -> bool:
    """Script boundary, not semantic language detection. Allows Latin names/diacritics."""
    return isinstance(value, str) and all(
        not c.isalpha() or "LATIN" in unicodedata.name(c, "") for c in value
    )
