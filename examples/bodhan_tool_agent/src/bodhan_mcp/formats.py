"""Structure-preserving document formats.

Translation never sends layout to the model. A document is parsed into a template of literal
pieces (pipes, table separators, heading markers, HTML tags, timestamps) and translatable slots.
Only slot text goes to Indic-Translate; the template is then refilled, so Markdown tables, HTML
tables, list markers, subtitle timings and segment JSON survive unchanged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .textproc import needs_translation


@dataclass
class Slot:
    text: str
    kind: str  # "block" (paragraph, may be long) | "cell" (table cell) | "line" (segment / cue)


@dataclass
class Template:
    fmt: str  # "markdown" | "segments" | "srt" | "vtt"
    pieces: list[str | Slot] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def slots(self) -> list[Slot]:
        return [p for p in self.pieces if isinstance(p, Slot)]

    def fill(self, translations: dict[int, str]) -> str:
        """Rebuild the document. ``translations`` maps slot ordinal -> translated text."""
        out: list[str] = []
        n = 0
        for p in self.pieces:
            if isinstance(p, Slot):
                out.append(translations.get(n, p.text))
                n += 1
            else:
                out.append(p)
        return "".join(out)


# ---------------------------------------------------------------------------
# Markdown / plain text
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"^\s*(```|~~~)")
_TABLE_ROW = re.compile(r"^\s*\|")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_PREFIXED = re.compile(r"^(\s*(?:#{1,6}\s+|[-*+]\s+|\d{1,3}[.)]\s+|[a-zA-Z][.)]\s+|>\s*|\(\w{1,3}\)\s+))(.*)$")
_HTML_COMMENT = re.compile(r"^\s*<!--.*-->\s*$")
_DISPLAY_MATH = re.compile(r"^\s*\$\$.*\$\$\s*$")
_HTML_TAG_SPLIT = re.compile(r"(<[^>]+>)")


def _slot_or_literal(text: str, kind: str, pieces: list) -> None:
    """Append ``text`` as a slot (keeping its surrounding whitespace literal) or as a literal."""
    if not needs_translation(text):
        pieces.append(text)
        return
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    if lead:
        pieces.append(lead)
    pieces.append(Slot(text.strip(), kind))
    if trail:
        pieces.append(trail)


def _table_row(line: str, pieces: list) -> None:
    if _TABLE_SEP.match(line):
        pieces.append(line)
        return
    parts = line.split("|")
    for i, cell in enumerate(parts):
        if i:
            pieces.append("|")
        _slot_or_literal(cell, "cell", pieces)


def _html_fragment(html: str, pieces: list) -> None:
    for part in _HTML_TAG_SPLIT.split(html):
        if not part:
            continue
        if part.startswith("<") and part.endswith(">"):
            pieces.append(part)
        else:
            _slot_or_literal(part, "cell", pieces)


def parse_markdown(text: str) -> Template:
    t = Template("markdown")
    lines = text.replace("\r\n", "\n").split("\n")
    i, n = 0, len(lines)
    pieces = t.pieces
    while i < n:
        line = lines[i]
        nl = "\n" if i < n - 1 else ""
        if _FENCE.match(line):  # code block: copy verbatim
            j = i + 1
            while j < n and not _FENCE.match(lines[j]):
                j += 1
            block = lines[i: j + 1]
            pieces.append("\n".join(block) + ("\n" if j < n - 1 else ""))
            i = j + 1
            continue
        if line.strip().lower().startswith("<table"):
            j = i
            while j < n and "</table>" not in lines[j].lower():
                j += 1
            j = min(j, n - 1)
            _html_fragment("\n".join(lines[i: j + 1]), pieces)
            pieces.append("\n" if j < n - 1 else "")
            i = j + 1
            continue
        if not line.strip() or _HTML_COMMENT.match(line) or _DISPLAY_MATH.match(line):
            pieces.append(line + nl)
            i += 1
            continue
        if _TABLE_ROW.match(line):
            _table_row(line, pieces)
            pieces.append(nl)
            i += 1
            continue
        if "<" in line and re.search(r"<(td|th|tr|br)\b", line, re.IGNORECASE):
            _html_fragment(line, pieces)
            pieces.append(nl)
            i += 1
            continue
        m = _PREFIXED.match(line)
        if m:
            pieces.append(m.group(1))
            _slot_or_literal(m.group(2), "block", pieces)
            pieces.append(nl)
            i += 1
            continue
        # A paragraph: consecutive plain lines are joined (OCR breaks lines mid-sentence).
        j = i
        para = []
        while j < n and lines[j].strip() and not (_TABLE_ROW.match(lines[j]) or _PREFIXED.match(lines[j]) or _FENCE.match(lines[j])
                                                    or lines[j].strip().lower().startswith("<table") or _HTML_COMMENT.match(lines[j])):
            para.append(lines[j].strip())
            j += 1
        _slot_or_literal(" ".join(para), "block", pieces)
        pieces.append("\n" if j < n else "")
        i = j
    return t


# ---------------------------------------------------------------------------
# Segment JSON (written by transcribe_audio)
# ---------------------------------------------------------------------------

def load_segments(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise ValueError("expected a JSON object with a 'segments' list (as written by transcribe_audio)")
    for s in data["segments"]:
        if not {"start", "end", "text"} <= set(s):
            raise ValueError("each segment needs 'start', 'end' and 'text'")
    return data


def parse_segments(data: dict) -> Template:
    t = Template("segments", meta=data)
    for s in data["segments"]:
        _slot_or_literal(str(s["text"]), "line", t.pieces)
        t.pieces.append("\x00")  # record separator, never sent to the model
    return t


def fill_segments(t: Template, translations: dict[int, str], target: str) -> str:
    filled = t.fill(translations).split("\x00")
    data = dict(t.meta)
    segs = []
    for s, new in zip(data["segments"], filled, strict=False):
        segs.append({**s, "text": new.strip(), "source_text": s.get("source_text", s["text"])})
    data["segments"] = segs
    data["language"] = target
    data["translated_from"] = t.meta.get("language")
    return json.dumps(data, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Subtitles
# ---------------------------------------------------------------------------

_TS = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})")


def _ts_to_s(ts: str) -> float:
    m = _TS.search(ts)
    if not m:
        return 0.0
    h, mi, s, ms = (int(g) for g in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000


def parse_subtitles(text: str) -> list[dict]:
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines or lines[0].strip().upper().startswith("WEBVTT"):
            continue
        idx = next((k for k, ln in enumerate(lines) if "-->" in ln), None)
        if idx is None:
            continue
        a, b = lines[idx].split("-->")[:2]
        cues.append({"start": _ts_to_s(a), "end": _ts_to_s(b), "text": " ".join(ln.strip() for ln in lines[idx + 1:])})
    return cues


def _fmt_ts(seconds: float, sep: str) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _wrap_two_lines(text: str, width: int = 42) -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    if len(lines) > 2:  # keep at most two lines per cue by rebalancing
        half = len(words) // 2
        lines = [" ".join(words[:half]), " ".join(words[half:])]
    return "\n".join(lines)


def format_subtitles(cues: list[dict], fmt: str = "srt") -> str:
    out = ["WEBVTT", ""] if fmt == "vtt" else []
    sep = "." if fmt == "vtt" else ","
    for i, c in enumerate(cues, 1):
        if fmt == "srt":
            out.append(str(i))
        out.append(f"{_fmt_ts(c['start'], sep)} --> {_fmt_ts(c['end'], sep)}")
        out.append(_wrap_two_lines(c["text"]))
        out.append("")
    return "\n".join(out)


def parse_subtitle_template(text: str, fmt: str) -> Template:
    cues = parse_subtitles(text)
    t = Template(fmt, meta={"cues": cues})
    for c in cues:
        _slot_or_literal(c["text"], "line", t.pieces)
        t.pieces.append("\x00")
    return t


def fill_subtitles(t: Template, translations: dict[int, str]) -> str:
    texts = t.fill(translations).split("\x00")
    cues = [{**c, "text": new.strip()} for c, new in zip(t.meta["cues"], texts, strict=False)]
    return format_subtitles(cues, t.fmt)


# ---------------------------------------------------------------------------
# Text to read aloud
# ---------------------------------------------------------------------------

def speech_text_from_markdown(text: str) -> str:
    """Strip Markdown/HTML syntax so TTS reads words, not symbols. Table rows become comma-separated phrases."""
    out = []
    for line in text.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if not s or _TABLE_SEP.match(s) or _HTML_COMMENT.match(s) or _FENCE.match(s):
            out.append("")
            continue
        if _TABLE_ROW.match(s):
            cells = [c.strip() for c in s.strip("|").split("|") if c.strip()]
            out.append(", ".join(cells) + ".")
            continue
        s = re.sub(r"<[^>]+>", " ", s)
        s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", s)
        s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
        s = re.sub(r"^\s*(#{1,6}|[-*+]|>)\s+", "", s)
        s = re.sub(r"[*_`~]+", "", s)
        out.append(s.strip())
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def text_from_file(path: Path) -> tuple[str, str]:
    """Read a text-like file. Returns (text, format) where format is markdown|segments|srt|vtt|text."""
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    ext = path.suffix.lower()
    if ext == ".json":
        try:
            data = load_segments(path)
            return "\n".join(str(s["text"]) for s in data["segments"]), "segments"
        except (ValueError, json.JSONDecodeError):
            return raw, "text"
    if ext in (".srt", ".vtt"):
        return "\n".join(c["text"] for c in parse_subtitles(raw)), ext[1:]
    if ext in (".md", ".markdown"):
        return raw, "markdown"
    if ext in (".html", ".htm"):
        return re.sub(r"<[^>]+>", " ", raw), "text"
    return raw, "text"
