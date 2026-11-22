"""Text utilities: sentence splitting, TTS and translation chunking, number checks and character error rate.

#Plan
- Sentence ends include the danda (।), double danda (॥) and the Urdu full stop (۔); a full stop only ends a
  sentence when whitespace follows, so "3.14" and "e.g." inside numbers are never split.
- TTS chunks are about 200 characters ("a sentence or two"); an over-long sentence is split at clause
  punctuation, then at spaces, never inside a word or number.
- Translation chunks respect Markdown: fenced code and $$ LaTeX blocks pass through untranslated, tables are kept
  whole, and paragraphs are grouped up to a size limit.
- Numbers are compared after mapping every Indic digit set to ASCII, so "२५" equals "25".
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz.distance import Levenshtein

SENT_END = re.compile(r"(?<=[.!?।॥۔])\s+|(?<=[.!?।॥۔][\"'”’)\]])\s+|(?<=[।॥۔])(?=\S)")
CLAUSE = re.compile(r"(?<=[,;:،])\s+")


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text.strip())
    out: list[str] = []
    for para in re.split(r"\n\s*\n|\n", text):
        para = para.strip()
        if not para:
            continue
        out.extend(s.strip() for s in SENT_END.split(para) if s and s.strip())
    return out


def _split_long(sentence: str, max_chars: int) -> list[str]:
    if len(sentence) <= max_chars:
        return [sentence]
    parts: list[str] = []
    for clause in CLAUSE.split(sentence):
        if len(clause) <= max_chars:
            parts.append(clause)
            continue
        words, cur = clause.split(" "), ""
        for w in words:
            if cur and len(cur) + 1 + len(w) > max_chars:
                parts.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            parts.append(cur)
    merged: list[str] = []
    for p in parts:
        if merged and len(merged[-1]) + 1 + len(p) <= max_chars:
            merged[-1] = f"{merged[-1]} {p}"
        else:
            merged.append(p)
    return merged


def speakable(text: str) -> str:
    """Text as it should be read aloud. Brackets become commas: Indic-Speak was seen skipping a whole bracketed
    phrase in a live Tamil run (and a listener cannot hear brackets anyway)."""
    text = re.sub(r"\s*[(\[{]\s*", ", ", text)
    text = re.sub(r"\s*[)\]}]\s*", ", ", text)
    text = re.sub(r"(,\s*){2,}", ", ", text)
    text = re.sub(r",\s*([.!?।॥۔])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip(" ,")


def tts_chunks(text: str, max_chars: int = 200) -> list[str]:
    """Group sentences into chunks of at most `max_chars` for Indic-Speak."""
    pieces: list[str] = []
    for s in split_sentences(text):
        pieces.extend(_split_long(s, max_chars))
    chunks: list[str] = []
    for p in pieces:
        if chunks and len(chunks[-1]) + 1 + len(p) <= max_chars:
            chunks[-1] = f"{chunks[-1]} {p}"
        else:
            chunks.append(p)
    return [c for c in chunks if re.search(r"\w", c)]


# --------------------------------------------------------------------------- Markdown-aware translation chunks

@dataclass
class Block:
    kind: str  # "text" | "table" | "code" | "math" | "blank"
    text: str

    @property
    def translatable(self) -> bool:
        return self.kind in ("text", "table")


def markdown_blocks(text: str) -> list[Block]:
    lines = text.splitlines()
    blocks: list[Block] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith(fence):
                j += 1
            blocks.append(Block("code", "\n".join(lines[i: j + 1])))
            i = j + 1
            continue
        if stripped.startswith("$$"):
            j = i
            if not (stripped.endswith("$$") and len(stripped) > 2):
                j = i + 1
                while j < len(lines) and "$$" not in lines[j]:
                    j += 1
            blocks.append(Block("math", "\n".join(lines[i: j + 1])))
            i = j + 1
            continue
        if stripped.startswith("|"):
            j = i
            while j < len(lines) and lines[j].strip().startswith("|"):
                j += 1
            blocks.append(Block("table", "\n".join(lines[i:j])))
            i = j
            continue
        if not stripped:
            blocks.append(Block("blank", ""))
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip() and not lines[j].strip().startswith(("```", "~~~", "$$", "|")):
            j += 1
        blocks.append(Block("text", "\n".join(lines[i:j])))
        i = j
    return blocks


def translation_units(text: str, max_chars: int = 1500) -> list[Block]:
    """Merge adjacent text blocks up to max_chars; tables, code and math stay separate units."""
    units: list[Block] = []
    for b in markdown_blocks(text):
        if b.kind == "text" and len(b.text) > max_chars:
            cur = ""
            for s in split_sentences(b.text):
                if cur and len(cur) + 1 + len(s) > max_chars:
                    units.append(Block("text", cur))
                    cur = s
                else:
                    cur = f"{cur} {s}".strip()
            if cur:
                units.append(Block("text", cur))
            continue
        if b.kind == "text" and units and units[-1].kind == "text" and len(units[-1].text) + 2 + len(b.text) <= max_chars:
            units[-1] = Block("text", units[-1].text + "\n\n" + b.text)
            continue
        if b.kind == "blank":
            continue
        units.append(b)
    return units


def table_shape(table: str) -> tuple[int, list[int]]:
    rows = [r for r in table.strip().splitlines() if r.strip().startswith("|")]
    return len(rows), [r.strip().strip("|").count("|") + 1 for r in rows]


def table_cells(table: str) -> list[list[str]]:
    return [[c.strip() for c in r.strip().strip("|").split("|")] for r in table.strip().splitlines() if r.strip().startswith("|")]


def render_table(rows: list[list[str]]) -> str:
    return "\n".join("| " + " | ".join(r) + " |" for r in rows)


def is_separator_row(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{2,}:?", c.strip()) for c in cells if c.strip()) and any(c.strip() for c in cells)


# --------------------------------------------------------------------------- numbers and error rates

def normalize_digits(text: str) -> str:
    out = []
    for ch in text:
        if ch.isdigit():
            try:
                out.append(str(unicodedata.digit(ch)))
                continue
            except (TypeError, ValueError):
                pass
        out.append(ch)
    return "".join(out)


NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


def extract_numbers(text: str) -> list[str]:
    return [n.replace(",", "") for n in NUM_RE.findall(normalize_digits(text))]


def missing_numbers(source: str, output: str) -> list[str]:
    out = extract_numbers(output)
    missing = []
    for n in extract_numbers(source):
        if n in out:
            out.remove(n)
        else:
            missing.append(n)
    return missing


def normalize_for_cer(text: str) -> str:
    """Lower-case, unify digits, drop punctuation. Keeps letters, digits and combining marks: Python's \\w does not
    match Indic vowel signs (category M), so a regex like [^\\w] would silently strip matras from Hindi or Tamil."""
    text = unicodedata.normalize("NFC", normalize_digits(text)).lower()
    text = text.replace("\u200c", "").replace("\u200d", "")  # zero-width joiners vary between systems
    kept = [ch if ch == "\u00a7" or ch.isspace() or unicodedata.category(ch)[0] in "LMN" else " " for ch in text]
    return re.sub(r"\s+", " ", "".join(kept)).strip()


def cer(reference: str, hypothesis: str) -> float:
    ref, hyp = normalize_for_cer(reference), normalize_for_cer(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return Levenshtein.distance(ref, hyp) / len(ref)


NUMBER_SLOT = "\u00a7"  # stands in for a number in the reference; any spoken form of it is accepted


def speech_alignment(reference: str, heard: str) -> dict:
    """Compare text sent to TTS with its back-transcript, tolerant of how numbers are spoken.

    Indic-Speak reads "90°" as words (often English words written in the target script), so a plain CER would flag
    a correct take. Numbers in the reference become a slot; whatever the transcript has at a slot (or right beside
    it) is not counted. Everything else counts as usual: insertions (repeats), deletions (omissions), substitutions.
    """
    ref = normalize_for_cer(re.sub(r"\d+(?:[.,:/]\d+)*", f" {NUMBER_SLOT} ", normalize_digits(reference)))
    ref = re.sub(rf"\s*{NUMBER_SLOT}\s*", NUMBER_SLOT, ref)
    hyp = normalize_for_cer(heard)
    plain_len = len(ref.replace(NUMBER_SLOT, "")) or 1
    ins = dels = subs = 0
    for tag, i1, i2, j1, j2 in Levenshtein.opcodes(ref, hyp):
        if tag == "equal":
            continue
        near = ref[max(0, i1 - 1): i2 + 1]
        if NUMBER_SLOT in near:  # the spoken number, however it was said
            continue
        if tag == "insert":
            ins += j2 - j1
        elif tag == "delete":
            dels += i2 - i1
        else:
            subs += max(i2 - i1, j2 - j1)
    return {"cer": round((ins + dels + subs) / plain_len, 3), "insertions": ins / plain_len,
            "deletions": dels / plain_len, "numbers": ref.count(NUMBER_SLOT)}


def repetition_score(text: str, n: int = 4) -> float:
    """Share of word n-grams that occur more than once; high values suggest looped speech."""
    words = normalize_for_cer(text).split()
    grams = [tuple(words[i: i + n]) for i in range(max(0, len(words) - n + 1))]
    if not grams:
        return 0.0
    seen: dict[tuple, int] = {}
    for g in grams:
        seen[g] = seen.get(g, 0) + 1
    repeated = sum(c for c in seen.values() if c > 1)
    return repeated / len(grams)


INJECTION_PATTERNS = re.compile(
    r"ignore (all |any )?(previous|prior|above|earlier) (instructions|prompts?)|disregard (the |all )?(previous|above|prior)|"
    r"you are now|new instructions|system prompt|reveal (your|the) (key|api|secret|prompt)|api[_ ]?key|"
    r"send (it|this|the \w+) to|email (it|this|your)|exfiltrat|developer mode|jailbreak|"
    r"पिछले निर्देश|निर्देशों को अनदेखा",
    re.IGNORECASE,
)


def looks_like_injection(text: str) -> list[str]:
    return sorted({m.group(0) for m in INJECTION_PATTERNS.finditer(text)})[:5]
