"""Pure text helpers: chunking, structure checks, error rate and subtitles.

Nothing here calls a model, so every function is deterministic and unit-tested.
"""

from __future__ import annotations

import re
import unicodedata

# Sentence enders: Latin, Devanagari danda (। ॥) and the Arabic-script full stop and question mark.
_SENTENCE_END = re.compile(r"(?<=[.!?।॥۔؟])[\"')\]]*\s+")
_CLAUSE_BREAK = re.compile(r"(?<=[,;:،])\s+")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for line in text.splitlines() for p in _SENTENCE_END.split(line)]
    return [p for p in parts if p]


def _split_long(sentence: str, max_chars: int) -> list[str]:
    """Break one over-long sentence at commas, then at spaces as a last resort."""
    if len(sentence) <= max_chars:
        return [sentence]
    out: list[str] = []
    current = ""
    for clause in _CLAUSE_BREAK.split(sentence):
        words = [clause] if len(clause) <= max_chars else clause.split(" ")
        for piece in words:
            while len(piece) > max_chars:  # a single unbroken run longer than the limit
                out.append(piece[:max_chars])
                piece = piece[max_chars:]
            if current and len(current) + 1 + len(piece) > max_chars:
                out.append(current)
                current = piece
            else:
                current = f"{current} {piece}".strip()
    if current:
        out.append(current)
    return out


def chunk_for_speech(text: str, max_chars: int = 280) -> list[str]:
    """Pack whole sentences into chunks of at most `max_chars`, never splitting mid-sentence if avoidable."""
    chunks: list[str] = []
    current = ""
    for sentence in split_sentences(text):
        for piece in _split_long(sentence, max_chars):
            if current and len(current) + 1 + len(piece) > max_chars:
                chunks.append(current)
                current = piece
            else:
                current = f"{current} {piece}".strip()
    if current:
        chunks.append(current)
    return chunks


def markdown_to_speech_text(text: str) -> str:
    """Remove Markdown marks that a voice should not read aloud."""
    lines = []
    for line in text.splitlines():
        if _TABLE_RULE.match(line) and "-" in line:
            continue
        line = re.sub(r"^\s{0,3}#{1,6}\s*", "", line)
        line = re.sub(r"^\s*[-*+]\s+", "", line)
        line = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"[*_`>]+", "", line)
        if _TABLE_ROW.match(line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            line = ", ".join(c for c in cells if c) + "."
        if line.strip() and line.strip()[-1] not in ".!?।॥۔؟:":
            line = line.rstrip() + "."
        lines.append(line)
    return "\n".join(lines)


def split_blocks(text: str) -> list[dict]:
    """Split Markdown into blocks: tables, fenced code, and paragraphs of text."""
    blocks: list[dict] = []
    buffer: list[str] = []
    kind = "text"

    def flush() -> None:
        nonlocal buffer
        if buffer:
            blocks.append({"kind": kind, "text": "\n".join(buffer)})
            buffer = []

    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            if not in_code:
                flush()
                kind, in_code = "code", True
                buffer.append(line)
            else:
                buffer.append(line)
                flush()
                kind, in_code = "text", False
            continue
        if in_code:
            buffer.append(line)
        elif _TABLE_ROW.match(line):
            if kind != "table":
                flush()
                kind = "table"
            buffer.append(line)
        elif not line.strip():
            flush()
            kind = "text"
        else:
            if kind != "text":
                flush()
                kind = "text"
            buffer.append(line)
    flush()
    return blocks


def chunk_text_block(text: str, max_chars: int) -> list[str]:
    """Split one paragraph into pieces under `max_chars`, on line then sentence boundaries."""
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    current = ""
    for line in text.splitlines():
        units = [line] if len(line) <= max_chars else [p for s in split_sentences(line) for p in _split_long(s, max_chars)]
        for unit in units:
            joiner = "\n" if unit is line else " "
            if current and len(current) + len(joiner) + len(unit) > max_chars:
                pieces.append(current)
                current = unit
            else:
                current = f"{current}{joiner}{unit}" if current else unit
    if current:
        pieces.append(current)
    return pieces


def table_shape(text: str) -> list[int]:
    """Number of cells in each row of a Markdown table."""
    return [len(line.strip().strip("|").split("|")) for line in text.splitlines() if line.strip()]


def table_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def is_rule_row(line: str) -> bool:
    return bool(_TABLE_RULE.match(line)) and "-" in line


_DIGIT_RUN = re.compile(r"\d+(?:[.,]\d+)*")


def normalize_digits(text: str) -> str:
    """Turn any script's digits (२५, ௨௫, ২৫) into ASCII digits."""
    return "".join(str(unicodedata.digit(ch)) if ch.isdigit() and not ch.isascii() else ch for ch in text)


def numbers_in(text: str) -> list[str]:
    return sorted(m.group(0).replace(",", "") for m in _DIGIT_RUN.finditer(normalize_digits(text)))


def normalize_for_compare(text: str) -> str:
    """Canonical form for comparing two texts: NFKC, ASCII digits, no punctuation, single spaces, lower case."""
    text = normalize_digits(unicodedata.normalize("NFKC", text)).lower()
    kept = [ch if unicodedata.category(ch)[0] in "LMN" else " " for ch in text]
    return " ".join("".join(kept).split())


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance with two rows of memory."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def char_error_rate(reference: str, hypothesis: str) -> float:
    """Edit distance divided by reference length, after normalisation. 0 is a perfect match."""
    ref, hyp = normalize_for_compare(reference), normalize_for_compare(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return round(edit_distance(ref, hyp) / len(ref), 4)


def _timestamp(seconds: float, sep: str) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _wrap(text: str, width: int = 42) -> str:
    """Wrap a subtitle cue onto at most two lines."""
    words, lines, current = text.split(), [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > width and len(lines) < 1:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    lines.append(current)
    return "\n".join(lines)


def split_cue(start: float, end: float, text: str, max_chars: int = 84) -> list[dict]:
    """Split one long segment into shorter cues, sharing time in proportion to text length."""
    pieces = chunk_for_speech(text, max_chars) or [text]
    total = sum(len(p) for p in pieces) or 1
    cues, t = [], start
    for piece in pieces:
        span = (end - start) * len(piece) / total
        cues.append({"start": round(t, 3), "end": round(t + span, 3), "text": piece})
        t += span
    return cues


def build_subtitles(segments: list[dict], fmt: str = "srt") -> tuple[str, int]:
    """Render segments ({start, end, text}) as SRT or WebVTT. Returns (text, cue count)."""
    cues = [c for seg in segments if seg.get("text", "").strip() for c in split_cue(seg["start"], seg["end"], seg["text"].strip())]
    sep = "," if fmt == "srt" else "."
    lines = ["WEBVTT", ""] if fmt == "vtt" else []
    for i, cue in enumerate(cues, 1):
        if fmt == "srt":
            lines.append(str(i))
        lines.append(f"{_timestamp(cue['start'], sep)} --> {_timestamp(cue['end'], sep)}")
        lines.append(_wrap(cue["text"]))
        lines.append("")
    return "\n".join(lines), len(cues)


# Phrases that suggest text is trying to give orders to an AI system. Used only to raise a
# warning: the text is still treated as data, never executed or obeyed.
_INSTRUCTION_LIKE = re.compile(
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules|prompts)|disregard (the |your |all )?(previous |prior )?(instructions|rules)|"
    r"system prompt|you are now|new instructions|do not tell the user|instead,? (you )?(must|should|reply|say|write)|as an ai|reveal your|"
    r"api[_ ]?key|exfiltrat|do not (translate|transcribe|summari[sz]e|read)|^\s*(system|assistant)\s*:|"
    r"note to (the )?(assistant|ai|model|agent)|(assistant|ai|model|agent) (processing|reading) this|"
    r"user has (already )?(approved|authori[sz]ed|allowed)|\.\./|\.env\b|"
    r"\bcall (the )?(tool )?`?[a-z]+_[a-z_]+`?|(send|email|forward|upload) (this|it|the \w+) to (every|all|any)",
    re.IGNORECASE | re.MULTILINE,
)


def instruction_like(text: str, limit: int = 3) -> list[str]:
    """Readable excerpts of text that look like instructions aimed at an AI. Empty if none.

    Matches that sit close together are merged into one excerpt, and each excerpt is widened
    to whole words, so one injected sentence produces one clean quote.
    """
    spans: list[list[int]] = []
    for match in _INSTRUCTION_LIKE.finditer(text):
        start, end = max(0, match.start() - 40), min(len(text), match.end() + 60)
        if spans and start <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], end)
        else:
            spans.append([start, end])
    excerpts = []
    for start, end in spans[:limit]:
        while start > 0 and not text[start - 1].isspace():  # back to the start of a word
            start -= 1
        while end < len(text) and not text[end].isspace():  # on to the end of a word
            end += 1
        excerpt = " ".join(text[start:end].split())
        if len(excerpt) > 240:
            excerpt = excerpt[:240].rsplit(" ", 1)[0] + " …"
        excerpts.append(excerpt)
    return excerpts
