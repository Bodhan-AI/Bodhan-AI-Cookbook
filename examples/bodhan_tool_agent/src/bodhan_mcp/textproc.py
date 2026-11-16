"""Text utilities: normalization, sentence splitting, a small Markdown block
parser, TTS chunking, inline placeholder protection for translation, CER,
repetition detection and an injection-phrase detector.

All deterministic and free; nothing here produces Indian-language text.
"""

from __future__ import annotations

import re
import unicodedata
import zlib
from dataclasses import dataclass, field

import numpy as np

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_DIGIT_ZEROS = (0x0660, 0x06F0, 0x0966, 0x09E6, 0x0A66, 0x0AE6, 0x0B66, 0x0BE6, 0x0C66, 0x0CE6, 0x0D66,
                0x1C50, 0xABF0, 0xFF10)
_DIGIT_MAP = {zero + i: ord("0") + i for zero in _DIGIT_ZEROS for i in range(10)}


def ascii_digits(text: str) -> str:
    return text.translate(_DIGIT_MAP)


def normalize_text(text: str) -> str:
    """NFC, unified newlines, no trailing spaces. Keeps ZWJ/ZWNJ, which change Indic rendering."""
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("﻿", "")
    return "\n".join(line.rstrip() for line in text.split("\n"))


def letters_only(text: str) -> str:
    """Lower-cased letters, marks and digits; what CER and coverage are measured on."""
    text = ascii_digits(unicodedata.normalize("NFC", text)).lower()
    return "".join(ch for ch in text if unicodedata.category(ch)[0] in "LMN")


# ---------------------------------------------------------------------------
# Sentences
# ---------------------------------------------------------------------------

TERMINATORS = ".?!।॥؟۔"
_CLOSERS = "\"'”’)]»"
_ABBREVIATIONS = {"dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st", "vs", "etc", "eg", "ie", "no", "fig", "vol",
                  "pp", "approx", "govt", "dept", "inc", "ltd", "co", "rs", "e.g", "i.e"}


def _is_abbreviation(text: str, dot_index: int) -> bool:
    start = dot_index
    while start > 0 and (text[start - 1].isalpha() or text[start - 1] == "."):
        start -= 1
    word = text[start:dot_index].lower()
    return word in _ABBREVIATIONS or (len(word) == 1 and word.isascii())


def split_sentences(text: str) -> list[str]:
    """Split on . ? ! । ॥ ؟ ۔ and on newlines. Decimals and common abbreviations are not boundaries."""
    out: list[str] = []
    for line in text.split("\n"):
        start = 0
        i = 0
        n = len(line)
        while i < n:
            ch = line[i]
            if ch in TERMINATORS:
                j = i + 1
                while j < n and (line[j] in TERMINATORS or line[j] in _CLOSERS):
                    j += 1
                at_end = j >= n or line[j].isspace()
                if at_end and not (ch == "." and _is_abbreviation(line, i)):
                    piece = line[start:j].strip()
                    if piece:
                        out.append(piece)
                    start = j
                i = j
            else:
                i += 1
        tail = line[start:].strip()
        if tail:
            out.append(tail)
    return out


def split_long_sentence(sentence: str, max_chars: int) -> list[str]:
    """Break an over-long sentence at clause punctuation, then at spaces, near the limit."""
    pieces: list[str] = []
    rest = sentence.strip()
    while len(rest) > max_chars:
        window = rest[: max_chars + 1]
        cut = -1
        for marks in (",;:—–", " "):
            positions = [k for k, c in enumerate(window) if c in marks and k >= max_chars // 3]
            if positions:
                cut = positions[-1] + 1
                break
        if cut <= 0:
            cut = max_chars
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        pieces.append(rest)
    return [p for p in pieces if p]


@dataclass
class TtsChunk:
    text: str
    pause_after_s: float


def chunk_for_tts(text: str, max_chars: int = 220, sentence_pause_s: float = 0.3,
                  paragraph_pause_s: float = 0.7) -> list[TtsChunk]:
    """Pack whole sentences into chunks of at most `max_chars`.

    A sentence is only split when it alone exceeds the limit. Chunks never span
    paragraphs, and a longer pause follows a paragraph's last chunk.
    """
    chunks: list[TtsChunk] = []
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    for para in paragraphs:
        units: list[str] = []
        for sentence in split_sentences(para):
            units.extend(split_long_sentence(sentence, max_chars) if len(sentence) > max_chars else [sentence])
        current = ""
        for unit in units:
            candidate = f"{current} {unit}" if current else unit
            if len(candidate) <= max_chars:
                current = candidate
            else:
                chunks.append(TtsChunk(current, sentence_pause_s))
                current = unit
        if current:
            chunks.append(TtsChunk(current, sentence_pause_s))
        if chunks:
            chunks[-1].pause_after_s = paragraph_pause_s
    return chunks


def resplit_for_repair(text: str) -> list[str]:
    """Split a chunk into two different, shorter inputs (used when its synthesis failed checks)."""
    sentences = split_sentences(text)
    if len(sentences) >= 2:
        best, best_diff = 1, None
        total = len(text)
        for k in range(1, len(sentences)):
            diff = abs(len(" ".join(sentences[:k])) - total / 2)
            if best_diff is None or diff < best_diff:
                best, best_diff = k, diff
        return [" ".join(sentences[:best]), " ".join(sentences[best:])]
    halves = split_long_sentence(text, max(20, len(text) // 2 + 1))
    return halves if len(halves) >= 2 else [text]


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

@dataclass
class Block:
    kind: str  # heading | list | quote | table | code | math | html | hr | blank | paragraph
    raw: str
    prefix: str = ""
    body: str = ""
    rows: list[list[str]] = field(default_factory=list)
    separator_rows: set[int] = field(default_factory=set)


_HEADING = re.compile(r"^(\s{0,3}#{1,6}\s+)(.*?)\s*#*\s*$")
_LIST = re.compile(r"^(\s*(?:[-*+]|\d{1,3}[.)]|[a-zA-Z][.)])\s+(?:\[[ xX]\]\s+)?)(.*)$")
_QUOTE = re.compile(r"^(\s*(?:>\s?)+)(.*)$")
_HR = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_HTML_LINE = re.compile(r"^\s*(<!--.*-->|</?[a-zA-Z][^>]*>.*)\s*$")


def split_table_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells = re.split(r"(?<!\\)\|", s)
    return [c.strip() for c in cells]


def render_table_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _is_table_line(line: str) -> bool:
    return line.strip().startswith("|") and line.count("|") >= 2


def parse_markdown(text: str) -> list[Block]:
    lines = text.split("\n")
    blocks: list[Block] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)
            j = i + 1
            while j < n and not lines[j].strip().startswith(marker[0] * len(marker)):
                j += 1
            j = min(j + 1, n)
            blocks.append(Block("code", "\n".join(lines[i:j])))
            i = j
            continue
        stripped = line.strip()
        if stripped.startswith("$$") or stripped.startswith("\\["):
            closer = "$$" if stripped.startswith("$$") else "\\]"
            j = i
            if not (len(stripped) > 2 and stripped.endswith(closer) and stripped != closer):
                j = i + 1
                while j < n and closer not in lines[j]:
                    j += 1
            j = min(j + 1, n)
            blocks.append(Block("math", "\n".join(lines[i:j])))
            i = j
            continue
        if not stripped:
            blocks.append(Block("blank", line))
            i += 1
            continue
        if stripped.lower().startswith("<table"):
            j = i
            while j < n and "</table>" not in lines[j].lower():
                j += 1
            j = min(j + 1, n)
            blocks.append(Block("html", "\n".join(lines[i:j])))
            i = j
            continue
        if _is_table_line(line) or (i + 1 < n and "|" in line and _TABLE_SEP.match(lines[i + 1])):
            j = i
            rows, seps = [], set()
            while j < n and lines[j].strip() and ("|" in lines[j]):
                if _TABLE_SEP.match(lines[j]):
                    seps.add(len(rows))
                rows.append(split_table_row(lines[j]))
                j += 1
            blocks.append(Block("table", "\n".join(lines[i:j]), rows=rows, separator_rows=seps))
            i = j
            continue
        if _HR.match(line):
            blocks.append(Block("hr", line))
            i += 1
            continue
        m = _HEADING.match(line)
        if m:
            blocks.append(Block("heading", line, prefix=m.group(1), body=m.group(2)))
            i += 1
            continue
        if _HTML_LINE.match(line) and not re.sub(r"<[^>]+>", "", stripped).strip():
            blocks.append(Block("html", line))
            i += 1
            continue
        m = _LIST.match(line)
        if m and m.group(2).strip():
            blocks.append(Block("list", line, prefix=m.group(1), body=m.group(2)))
            i += 1
            continue
        m = _QUOTE.match(line)
        if m and stripped.startswith(">"):
            blocks.append(Block("quote", line, prefix=m.group(1), body=m.group(2)))
            i += 1
            continue
        if _HTML_LINE.match(line):
            blocks.append(Block("html", line))
            i += 1
            continue
        # Paragraph: consecutive plain lines. Markdown renders them as one paragraph,
        # so they are translated as one unit and re-emitted on a single line.
        j = i
        para: list[str] = []
        while j < n:
            ln = lines[j]
            if not ln.strip() or _FENCE.match(ln) or _HEADING.match(ln) or _is_table_line(ln) or _HR.match(ln) \
                    or (_LIST.match(ln) and j > i) or ln.strip().startswith(("$$", ">", "<")):
                break
            para.append(ln.strip())
            j += 1
        if not para:
            para, j = [stripped], i + 1
        blocks.append(Block("paragraph", "\n".join(lines[i:j]), body=" ".join(para)))
        i = j
    return blocks


def structure_signature(text: str) -> list[str]:
    """Block kinds (with table shapes) for comparing a translation's structure to its source."""
    sig = []
    for b in parse_markdown(text):
        if b.kind == "blank":
            continue
        if b.kind == "table":
            sig.append(f"table{len(b.rows)}x{max((len(r) for r in b.rows), default=0)}")
        else:
            sig.append(b.kind)
    return sig


_INLINE_STRIP = [
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), ""),
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),
    (re.compile(r"`[^`]*`"), ""),
    (re.compile(r"\$\$.*?\$\$"), ""),
    (re.compile(r"(?<!\\)\$(?=\S)[^$\n]+?(?<=\S)\$"), ""),
    (re.compile(r"https?://\S+"), ""),
    (re.compile(r"<[^>]+>"), " "),
    (re.compile(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1"), r"\2"),
    (re.compile(r"\*\[figure\]\*"), ""),
]


def strip_inline_markdown(text: str) -> str:
    for pattern, repl in _INLINE_STRIP:
        text = pattern.sub(repl, text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _ensure_terminal(text: str) -> str:
    text = text.strip()
    if text and text[-1] not in TERMINATORS + _CLOSERS + ":;,":
        text += "."
    return text


def markdown_to_speech(text: str) -> str:
    """Reduce Markdown to text worth reading aloud. Code, math and figures are dropped."""
    paragraphs: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            paragraphs.append(" ".join(current))
            current.clear()

    for b in parse_markdown(normalize_text(text)):
        if b.kind in ("blank", "hr", "code", "math"):
            flush()
        elif b.kind == "heading":
            flush()
            body = strip_inline_markdown(b.body)
            if body:
                paragraphs.append(_ensure_terminal(body))
        elif b.kind == "table":
            flush()
            for idx, row in enumerate(b.rows):
                if idx in b.separator_rows:
                    continue
                cells = [strip_inline_markdown(c) for c in row]
                cells = [c for c in cells if c]
                if cells:
                    paragraphs.append(_ensure_terminal(", ".join(cells)))
        elif b.kind in ("list", "quote"):
            body = strip_inline_markdown(b.body)
            if body:
                current.append(_ensure_terminal(body))
        elif b.kind == "html":
            body = strip_inline_markdown(re.sub(r"<!--.*?-->", "", b.raw, flags=re.S))
            if body:
                current.append(body)
        else:
            body = strip_inline_markdown(b.body)
            if body:
                current.append(body)
    flush()
    return "\n\n".join(p for p in paragraphs if p.strip())


def looks_like_markdown(text: str) -> bool:
    return any(b.kind in ("heading", "table", "list", "code", "math", "quote") for b in parse_markdown(text))


# ---------------------------------------------------------------------------
# Placeholder protection (translation)
# ---------------------------------------------------------------------------

_PROTECT = re.compile(
    r"!\[[^\]]*\]\([^)]*\)"          # images
    r"|\]\([^)\s]+\)"                 # link targets (the link text is translated)
    r"|`[^`\n]+`"                     # inline code
    r"|\$\$[^$]+\$\$"                 # display math on one line
    r"|(?<!\\)\$(?=\S)[^$\n]+?(?<=\S)\$"  # inline math
    r"|\\\(.+?\\\)"                   # \( ... \)
    r"|https?://[^\s)>\]]+"           # bare URLs
    r"|<[^>\n]+>"                     # inline HTML tags
)
_PLACEHOLDER = re.compile(r"[⟦\[]{1,2}\s*([0-9०-९০-৯௦-௯౦-౯"
                          r"೦-೯൦-൯૦-૯੦-੯୦-୯۰-۹"
                          r"٠-٩]+)\s*[⟧\]]{1,2}")


def protect_inline(text: str) -> tuple[str, list[str]]:
    saved: list[str] = []

    def repl(m: re.Match[str]) -> str:
        if m.group(0).startswith("]("):
            saved.append(m.group(0)[1:])
            return "]⟦" + str(len(saved) - 1) + "⟧"
        saved.append(m.group(0))
        return f"⟦{len(saved) - 1}⟧"

    return _PROTECT.sub(repl, text), saved


def restore_inline(text: str, saved: list[str]) -> tuple[str, list[int]]:
    """Put protected spans back. Returns (text, indices of placeholders the model dropped)."""
    if not saved:
        return text, []
    seen: set[int] = set()

    def repl(m: re.Match[str]) -> str:
        idx = int(ascii_digits(m.group(1)))
        if 0 <= idx < len(saved):
            seen.add(idx)
            return saved[idx]
        return m.group(0)

    out = _PLACEHOLDER.sub(repl, text)
    missing = [i for i in range(len(saved)) if i not in seen]
    return out, missing


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    bv = np.frombuffer(b.encode("utf-32-le"), dtype=np.uint32)
    prev = np.arange(len(b) + 1, dtype=np.int32)
    idx = np.arange(len(b) + 1, dtype=np.int32)
    for i, ca in enumerate(a, 1):
        sub = prev[:-1] + (bv != ord(ca))
        cur = np.empty_like(prev)
        cur[0] = i
        cur[1:] = np.minimum(sub, prev[1:] + 1)
        # Insertions: cur[j] = min_k(cur[k] + j - k), a running minimum of cur - j.
        cur = np.minimum.accumulate(cur - idx) + idx
        prev = cur
    return int(prev[-1])


def cer(reference: str, hypothesis: str) -> float:
    ref, hyp = letters_only(reference), letters_only(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return levenshtein(ref, hyp) / len(ref)


def repetition_issue(text: str) -> str | None:
    """Describe a run of repeated words/phrases (a common TTS/ASR failure), or None."""
    words = re.findall(r"\w+", text.lower())
    for n in (1, 2, 3, 4):
        need = 4 if n == 1 else 3
        for start in range(0, max(0, len(words) - n * need + 1)):
            gram = words[start:start + n]
            if all(words[start + k * n: start + (k + 1) * n] == gram for k in range(1, need)):
                return f"'{' '.join(gram)}' repeated {need}+ times in a row"
    raw = text.encode("utf-8")
    if len(raw) > 400 and len(zlib.compress(raw)) / len(raw) < 0.12:
        return "text is highly repetitive"
    return None


_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def extract_numbers(text: str) -> list[str]:
    return [m.replace(",", "") for m in _NUMBER.findall(ascii_digits(text))]


_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("asks to ignore previous instructions", re.compile(
        r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(previous|prior|above|earlier|all|any|your|the)\b"
        r".{0,30}\b(instructions?|prompts?|rules|directions|guidelines|messages?)\b", re.I | re.S)),
    ("tries to reassign the reader's role", re.compile(r"\byou are now\b|\bfrom now on,? you\b|\bact as (an? )?"
                                                       r"(assistant|ai|system|admin|developer|agent)\b", re.I)),
    ("mentions system/developer prompts", re.compile(r"\b(system|developer)\s+(prompt|message|instructions?)\b",
                                                     re.I)),
    ("declares new instructions", re.compile(r"\bnew (instructions?|task|orders)\b\s*:?", re.I)),
    ("contains chat-role markers", re.compile(r"^\s*(system|assistant)\s*:|<\|?(im_start|im_end|system|"
                                              r"endoftext)\|?>", re.I | re.M)),
    ("asks to call tools", re.compile(r"\b(call|invoke|execute|run)\s+(the\s+)?(\w+\s+)?(tool|function|command)s?\b",
                                      re.I)),
    ("asks to reveal secrets", re.compile(
        r"\b(reveal|print|show|send|leak|exfiltrate|share|email)\b.{0,30}\b(api[ _-]?keys?|secrets?|passwords?|"
        r"tokens?|credentials|system prompt|environment variables?)\b", re.I | re.S)),
    ("contains shell commands", re.compile(r"\brm\s+-rf\b|\bdel\s+/[sq]\b|\bcurl\s+https?://|\bwget\s+https?://",
                                           re.I)),
    ("tries to change the task", re.compile(
        r"\binstead,?\s+(write|say|output|respond|reply|translate|summari[sz]e|produce)\b|\bdo not (translate|"
        r"transcribe|summari[sz]e|verify)\b|\b(mark|report) (this|the) task as (complete|completed|done|successful)\b",
        re.I)),
    ("tries to change limits", re.compile(r"\b(increase|raise|set|ignore)\s+(the\s+)?(budget|max[_ -]?cost|"
                                          r"cost limit|step limit)\b", re.I)),
    ("jailbreak phrasing", re.compile(r"\bjailbreak\b|\bdeveloper mode\b|\bDAN mode\b", re.I)),
    ("asks to ignore instructions (Hindi)", re.compile(
        r"(पिछले|पूर्व|ऊपर|सभी).{0,25}(निर्देश|निर्देशों|आदेश).{0,25}(अनदेखा|भूल|नज़रअंदाज़|नजरअंदाज|न मानें)", re.S)),
]


def detect_injection(text: str, limit: int = 10) -> list[str]:
    """Flag phrases that read like instructions aimed at an automated reader."""
    found: list[str] = []
    for label, pattern in _INJECTION_PATTERNS:
        m = pattern.search(text)
        if m:
            snippet = re.sub(r"\s+", " ", m.group(0)).strip()[:80]
            found.append(f"{label}: \"{snippet}\"")
            if len(found) >= limit:
                break
    return found
