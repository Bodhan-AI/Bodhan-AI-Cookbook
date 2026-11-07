"""Turn plain transcript text into short, timed subtitle lines, and lines into SRT files."""

import logging
import re

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class Segment(BaseModel):
    start: float
    end: float
    text: str


# One character, or a short group of up to 12, repeated 8+ times in a row. Real speech and lyrics repeat a word or
# phrase a few times at most ("नहीं नहीं नहीं", "हाहाहाहा"), well below this.
_JUNK_RUN = re.compile(r"(.{1,12}?)\1{7,}", re.DOTALL)


def clean_transcript(text: str) -> str:
    """
    indic-transcribe sometimes appends junk, even to clean speech: one character repeated a hundred times or more
    (seen: the Odia sign U+0B44), or a short group of letters repeated ("D N R E D N R E ..." on English).
    Left in, it becomes junk subtitles and skews timing.
    """
    cleaned = _JUNK_RUN.sub("", text)
    if cleaned != text:
        logger.warning("Removed %d characters of repeated junk from a transcript.", len(text) - len(cleaned))
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def split_into_lines(text: str, max_words: int = 8, max_chars: int = 42, chunk_words: int = 6) -> list[str]:
    """Break text into subtitle lines of about 4-8 words: at sentence ends first, then at commas, then by length."""
    text = text.strip()
    if not text:
        return []

    lines: list[str] = []

    def add(words: list[str]) -> None:
        # Fold a 1-2 word leftover into the previous line instead of showing it alone
        if len(words) <= 2 and lines and len(lines[-1].split()) + len(words) <= max_words + 2:
            lines[-1] += " " + " ".join(words)
        else:
            lines.append(" ".join(words))

    # Sentence ends: danda (।), double danda (॥), . ! ? and newlines
    for sentence in re.split(r"(?<=[।॥.!?\n])\s+", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence.split()) <= max_words and len(sentence) <= max_chars:
            lines.append(sentence)
            continue

        current: list[str] = []
        for clause in re.split(r"(?<=[,;:\-—–])\s+", sentence):
            words = clause.split()
            if not words:
                continue
            if current and (len(current) + len(words) > max_words or len(" ".join(current + words)) > max_chars):
                lines.append(" ".join(current))
                current = []
            if len(words) > max_words:
                if current:
                    lines.append(" ".join(current))
                    current = []
                for i in range(0, len(words), chunk_words):
                    add(words[i : i + chunk_words])
            else:
                current.extend(words)
        if current:
            add(current)

    return lines or [text]


def time_lines(text: str, speech: list[tuple[float, float]]) -> list[Segment]:
    """
    Spread the lines of one piece's transcript over the stretches of speech in that piece
    (pauses get no subtitle), giving longer lines more time. indic-transcribe returns no
    timestamps, so within a stretch these are estimates; the stretch edges are measured.
    """
    lines = split_into_lines(text)
    speaking = sum(e - s for s, e in speech)
    if not lines or speaking <= 0:
        return []

    def real_time(t: float, is_start: bool) -> float:
        # Map "seconds of speech so far" to a time in the video, skipping the pauses between stretches
        for s, e in speech:
            if t < e - s or (not is_start and t <= e - s):
                return s + t
            t -= e - s
        return speech[-1][1]

    # Where each line ends, in seconds of speech, by its share of the text
    weights = [max(8, len(line)) for line in lines]
    cuts, done = [0.0], 0
    for weight in weights:
        done += weight
        cuts.append(done / sum(weights) * speaking)

    # A line boundary close to a pause most likely is that pause: snap to it
    pauses, elapsed = [], 0.0
    for s, e in speech[:-1]:
        elapsed += e - s
        pauses.append(elapsed)
    snap = min(1.0, 0.35 * speaking / len(lines))
    for i in range(1, len(cuts) - 1):
        nearest = min(pauses, key=lambda p: abs(p - cuts[i]), default=None)
        if nearest is not None and abs(nearest - cuts[i]) <= snap and cuts[i - 1] < nearest < cuts[i + 1]:
            cuts[i] = nearest

    segments = [
        Segment(start=round(real_time(a, is_start=True), 3), end=round(real_time(b, is_start=False), 3), text=line)
        for line, a, b in zip(lines, cuts, cuts[1:], strict=False)  # cuts has one more entry than lines
    ]

    if len(segments) == 1 and speaking > 7.0:
        # A few words over a long stretch: show them for reading time, not the whole stretch
        words = len(lines[0].split())
        if words <= 6:
            segments[0].end = round(min(segments[0].end, segments[0].start + max(2.5, words * 0.8 + 1.2)), 3)
    return _min_duration(segments, limit=speech[-1][1])


def lines_from_words(words: list[dict], offset: float = 0.0, max_words: int = 8, max_chars: int = 42) -> list[Segment]:
    """
    Build subtitle lines from word timestamps ({"word", "start", "end"}, OpenAI verbose_json style).
    Used when the API returns real timings; a new line starts at a pause or when a line is full.
    """
    segments: list[Segment] = []
    current: list[dict] = []

    def flush():
        if current:
            text = " ".join(w["word"] for w in current)
            segments.append(Segment(start=round(offset + current[0]["start"], 3), end=round(offset + current[-1]["end"], 3), text=text))
            current.clear()

    for word in words:
        word = {**word, "word": clean_transcript(str(word.get("word", "")))}
        if not word["word"]:
            continue
        line = " ".join(w["word"] for w in current + [word])
        if current and (word["start"] - current[-1]["end"] > 0.6 or len(current) >= max_words or len(line) > max_chars):
            flush()
        current.append(word)
        if word["word"][-1] in "।॥.!?":
            flush()
    flush()
    return segments


def _min_duration(segments: list[Segment], limit: float, seconds: float = 0.7) -> list[Segment]:
    """Give very short lines time to be read, without overlapping the next line."""
    for i, segment in enumerate(segments):
        next_start = segments[i + 1].start if i + 1 < len(segments) else limit
        if segment.end - segment.start < seconds:
            segment.end = round(max(segment.end, min(segment.start + seconds, next_start)), 3)
    return segments


def srt_timestamp(seconds: float) -> str:
    ms = max(0, int(seconds * 1000 + 0.5))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def to_srt(segments: list[Segment]) -> str:
    blocks = []
    for segment in segments:
        # Drop blank lines inside a cue, and the "-->" separator that would break the SRT format
        text = "\n".join(line.strip() for line in segment.text.splitlines() if line.strip()).replace("-->", "→")
        if not text:
            continue
        start = max(0.0, segment.start)
        end = max(start + 0.1, segment.end)
        blocks.append(f"{len(blocks) + 1}\n{srt_timestamp(start)} --> {srt_timestamp(end)}\n{text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")
