"""Local format conversion only: no OCR, ASR, translation or TTS models here."""

from __future__ import annotations
import unicodedata
import json
import re
import subprocess
import wave
import math
import textwrap
import time
import difflib
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


def audio_chunks(
    path: Path, work: Path, timeout: float = 45, *, prefer_pauses: bool = False,
    window_seconds: int = 25,
) -> list[tuple[Path, float, float]]:
    if type(window_seconds) is not int or not 5 <= window_seconds <= 30:
        raise ToolError("audio_window", "ASR window must be an integer between 5 and 30 seconds.")
    deadline = time.monotonic() + timeout

    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise ToolError("media_timeout", "Audio conversion/analysis time budget exhausted.")
        return seconds

    duration(path, remaining())
    variant = f"_audio_{'pause' if prefer_pauses else 'fixed'}_{window_seconds}"
    if not prefer_pauses and window_seconds == 25:
        variant = "_audio"
    folder = work / (digest(path.read_bytes())[:16] + variant)
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
        remaining(),
    )
    segments = []
    pauses = []
    if prefer_pauses:
        metadata = command([
            "ffmpeg", "-v", "error", "-i", str(normalized_audio),
            "-af", "silencedetect=noise=-35dB:duration=0.25,ametadata=mode=print:file=-",
            "-f", "null", "-",
        ], remaining()).decode("utf-8", errors="replace")
        silence_start = None
        for kind, value in re.findall(r"lavfi\.silence_(start|end)=([0-9.]+)", metadata):
            if kind == "start":
                silence_start = float(value)
            elif silence_start is not None:
                pauses.append((silence_start + float(value)) / 2)
                silence_start = None
    with wave.open(str(normalized_audio), "rb") as source:
        frame_rate = source.getframerate()
        total_frames = source.getnframes()
        frames_per_chunk = window_seconds * frame_rate
        pause_frames = [round(value * frame_rate) for value in pauses]
        start_frame, index = 0, 0
        while start_frame < total_frames:
            end_frame = min(start_frame + frames_per_chunk, total_frames)
            if end_frame < total_frames:
                candidates = [p for p in pause_frames if start_frame + window_seconds * 0.6 * frame_rate <= p <= end_frame]
                if candidates:
                    end_frame = max(candidates)
            data = source.readframes(end_frame - start_frame)
            part = folder / f"{index:05d}.wav"
            with wave.open(str(part), "wb") as dest:
                dest.setparams(source.getparams())
                dest.writeframes(data)
            segments.append((part, start_frame / frame_rate, end_frame / frame_rate))
            start_frame, index = end_frame, index + 1
    return segments


def timing_audio_windows(path: Path, work: Path, seconds: int = 5) -> list[tuple[Path, float, float]]:
    """Balanced exact PCM windows; the caller supplies an already decoded WAV."""
    if type(seconds) is not int or not 5 <= seconds <= 30:
        raise ToolError("audio_window", "Timing windows must be integer seconds between 5 and 30.")
    folder = work / (digest(path.read_bytes())[:16] + f"_timing_{seconds}")
    folder.mkdir(exist_ok=True)
    windows = []
    with wave.open(str(path), "rb") as source:
        frames, rate = source.getnframes(), source.getframerate()
        if not frames:
            raise ToolError("invalid_media", "Timing source WAV is empty.")
        count = math.ceil(frames / (seconds * rate))
        start = 0
        for index in range(count):
            end = round(frames * (index + 1) / count)
            target = folder / f"{index:05d}.wav"
            with wave.open(str(target), "wb") as output:
                output.setparams(source.getparams())
                output.writeframes(source.readframes(end - start))
            windows.append((target, start / rate, end / rate))
            start = end
    return windows


def align_text_windows(reference: str, anchors: list[dict]) -> list[dict]:
    """Copy contextual words into monotonic recognized windows, never invent times."""
    words = reference.split()

    def normalized(value):
        return "".join(
            c for c in unicodedata.normalize("NFKC", value).casefold()
            if unicodedata.category(c)[0] in "LNM"
        )

    observed, owners = [], []
    last = 0.0
    for index, anchor in enumerate(anchors):
        start, end = anchor["start"], anchor["end"]
        if not math.isfinite(start) or not math.isfinite(end) or start < last or end <= start:
            raise ToolError("subtitle_alignment", "Timing anchors must be finite and ordered.")
        tokens = anchor["text"].split()
        observed.extend(tokens)
        owners.extend([index] * len(tokens))
        last = end
    if not words or not observed:
        raise ToolError("subtitle_alignment", "Nonempty reference and recognized anchors are required.")
    a, b = list(map(normalized, words)), list(map(normalized, observed))
    if len(a) * len(b) > 250000:
        raise ToolError("subtitle_alignment", "Timing anchor text exceeds the bounded alignment work limit.")
    rows = [[j for j in range(len(b) + 1)]]
    for i in range(1, len(a) + 1):
        row = [i]
        for j in range(1, len(b) + 1):
            row.append(min(rows[-1][j - 1] + (a[i - 1] != b[j - 1]), rows[-1][j] + 1, row[-1] + 1))
        rows.append(row)
    mapping = [None] * len(a)
    i, j, matched = len(a), len(b), 0
    while i or j:
        if i and j and rows[i][j] == rows[i - 1][j - 1] + (a[i - 1] != b[j - 1]):
            mapping[i - 1] = owners[j - 1]
            matched += a[i - 1] == b[j - 1]
            i, j = i - 1, j - 1
        elif i and rows[i][j] == rows[i - 1][j] + 1:
            i -= 1
        else:
            j -= 1
    if matched / max(len(a), len(b)) < 0.45:
        raise ToolError("subtitle_alignment", "Recognized anchors do not sufficiently support the contextual text.")
    # Missing contextual words follow the next recognized word, preserving articles
    # with the following phrase; trailing words stay with the last supported window.
    following = next((owner for owner in reversed(mapping) if owner is not None), None)
    for index in reversed(range(len(mapping))):
        if mapping[index] is None:
            mapping[index] = following
        else:
            following = mapping[index]
    grouped = [[] for _ in anchors]
    for word, owner in zip(words, mapping):
        if owner is None:
            raise ToolError("subtitle_alignment", "A contextual word has no recognized timing anchor.")
        grouped[owner].append(word)
    # Edit-distance substitutions can strand a short sentence opening before its
    # supported phrase. Keep that fragment with the following nonempty window;
    # punctuation and original words supply the boundary, not language generation.
    occupied = [index for index, tokens in enumerate(grouped) if tokens]
    for previous, following in zip(occupied, occupied[1:]):
        tokens = grouped[previous]
        endings = [i for i, token in enumerate(tokens) if re.search(r"[.!?।॥][\"'’”)]*$", token)]
        if endings:
            boundary = endings[-1] + 1
            if 0 < len(tokens) - boundary <= 2:
                grouped[following][:0] = tokens[boundary:]
                del tokens[boundary:]
    result = []
    for anchor, tokens in zip(anchors, grouped):
        if tokens:
            similarity = difflib.SequenceMatcher(None, list(map(normalized, tokens)), list(map(normalized, anchor["text"].split()))).ratio()
            if similarity < 0.3:
                raise ToolError("subtitle_alignment", "A timing anchor is too weak for its contextual phrase.")
            result.append({"start": anchor["start"], "end": anchor["end"], "text": " ".join(tokens)})
    return result


def subtitle_cues(text: str, start: float, end: float) -> list[dict]:
    """Readable verbatim text with estimated timing, never word alignment."""
    if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end:
        raise ToolError("subtitle_timing", "Subtitle source window must have positive finite duration.")
    lines = textwrap.wrap(plain_text(text), 48, break_long_words=False, break_on_hyphens=False)
    if not lines:
        raise ToolError("empty_translation", "Cannot create a caption from empty translated text.")
    groups = [" ".join(lines[i:i + 2]) for i in range(0, len(lines), 2)]
    first, last = round(start * 1000), round(end * 1000)
    if last <= first:
        raise ToolError("subtitle_timing", "Source window is shorter than one millisecond.")
    wanted = min(last - first, math.ceil((last - first) / 6000))
    while len(groups) < wanted:
        splittable = [(len(value), i) for i, value in enumerate(groups) if len(value.split()) > 1]
        if not splittable:
            break
        _, index = max(splittable)
        words = groups[index].split()
        middle = len(words) // 2
        groups[index:index + 1] = [" ".join(words[:middle]), " ".join(words[middle:])]
    # A very short window may not support a distinct millisecond per text group.
    if len(groups) > last - first:
        groups = [" ".join(groups)]
    return [
        {
            "start": (first + round((last - first) * i / len(groups))) / 1000,
            "end": (first + round((last - first) * (i + 1) / len(groups))) / 1000,
            "text": "\n".join(textwrap.wrap(value, 48, break_long_words=False, break_on_hyphens=False)),
        }
        for i, value in enumerate(groups)
    ]


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
