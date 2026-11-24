"""Deterministic text analysis, script/stopword LID, chunking, CER, repetition, table checks, and SRT/VTT."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from bodhan_mcp.rules import LANGUAGE_PRIMARY_SCRIPT, SCRIPT_RANGES, language_names

# High-frequency function words for shared-script disambiguation (Devanagari, Bengali, Arabic).
DEVANAGARI_STOPWORDS: dict[str, set[str]] = {
    "hi": {
        "है", "हैं", "था", "थी", "थे", "का", "की", "के", "में", "को", "से", "पर",
        "और", "यह", "वह", "इस", "उस", "नहीं", "कि", "जो", "तो", "भी", "एक",
        "कर", "किया", "गया", "लिए", "साथ", "होगा", "रहा", "रही", "हम", "आप", "मैं",
    },
    "mr": {
        "आहे", "आहेत", "होता", "होती", "होते", "मध्ये", "साठी", "नाही", "पण", "किंवा",
        "त्या", "ती", "ते", "हा", "ही", "हे", "आणि", "करून", "केले", "असून",
        "या", "वरून", "पासून", "मला", "तुम्ही", "आम्ही", "झाले", "येथे", "सर्व",
    },
    "ne": {
        "छ", "छन्", "थियो", "थिए", "हो", "हुन्", "र", "मा", "को", "लाई", "ले",
        "बाट", "गर्न", "गरेको", "भने", "पनि", "तर", "यो", "त्यो", "हामी", "तपाईं",
        "छैन", "हुन्छ", "भएको", "लागि",
    },
    "sa": {
        "अस्ति", "सन्ति", "च", "तथा", "एव", "इति", "भवति", "तस्य", "अस्य",
        "यत्", "तत्", "न", "तु", "हि", "अपि", "कथम्", "एतत्", "सर्वम्", "कृते",
    },
    "mai": {
        "अछि", "छथि", "मे", "सँ", "कऽ", "आ", "ई", "ओ", "नहि", "जे", "तँ",
        "हम", "अहाँ", "भेल", "कएल", "लेल",
    },
}

BENGALI_STOPWORDS: dict[str, set[str]] = {
    "bn": {
        "আছে", "এবং", "করে", "জন্য", "হবে", "এই", "থেকে", "না", "যে", "ও",
        "একটি", "তার", "তারা", "আমরা", "আমি", "কিন্তু", "হয়", "করা", "ছিল", "দিয়ে",
    },
    "as": {
        "আছে", "আৰু", "কৰি", "বাবে", "হব", "এইটো", "পৰা", "নহয়", "যি", "এটা",
        "তেওঁ", "আমি", "মই", "কিন্তু", "হয়", "কৰা", "আছিল", "দিয়া", "হৈছে", "কৰে",
    },
}

ARABIC_STOPWORDS: dict[str, set[str]] = {
    "ur": {
        "ہے", "ہیں", "تھا", "تھی", "کے", "کی", "کا", "میں", "کو", "سے",
        "اور", "یہ", "وہ", "نہیں", "پر", "ایک", "بھی", "کر", "لیا", "گیا",
    },
    "ks": {"چھُ", "چھی", "تہٕ", "مَنٛز", "پؠٹھ", "یُس", "سُہ"},
    "sd-Arab": {"آهي", "آهن", "۾", "جو", "جي", "جا", "۽", "ته", "نه", "کان"},
}

INDIC_DIGIT_TRANS = str.maketrans(
    "०१२३४५६७८९"  # Devanagari
    "০১২৩৪৫৬৭৮৯"  # Bengali
    "૦૧૨૩૪૫૬૭૮૯"  # Gujarati
    "੦੧੨੩੪੫੬੭੮੯"  # Gurmukhi
    "୦୧୨୩୪୫୬୭୮୯"  # Oriya
    "௦௧௨௩௪௫௬௭௮௯"  # Tamil
    "౦౧౨౩౪౫౬౭౮౯"  # Telugu
    "೦೧೨೩೪೫೬೭೮೯"  # Kannada
    "൦൧൨൩൪൫൬൭EightNine".replace("Eight", "൮").replace("Nine", "൯")
    + "۰۱۲۳۴۵۶۷۸۹",  # Perso-Arabic
    "0123456789" * 10,
)

SENTENCE_SPLIT_RE = re.compile(r"(?<=[।॥.!?؟])\s+|\n+")
INDIC_SCRIPTS = {name for name, _, _, _ in SCRIPT_RANGES if name != "Latin"}


def _char_script(ch: str) -> str | None:
    cp = ord(ch)
    for name, lo, hi, _ in SCRIPT_RANGES:
        if lo <= cp <= hi:
            return name
    return None


def script_histogram(text: str) -> dict[str, float]:
    """Compute normalized distribution of Unicode scripts across letter characters."""
    counts: dict[str, int] = {}
    total = 0
    for ch in text:
        if not ch.isalpha():
            continue
        sc = _char_script(ch)
        if sc:
            counts[sc] = counts.get(sc, 0) + 1
            total += 1
    if total == 0:
        return {}
    return {k: round(v / total, 4) for k, v in sorted(counts.items(), key=lambda kv: -kv[1])}


def contains_indic_script(text: str) -> bool:
    """Return True if text contains any Indian-language Unicode script characters."""
    for ch in text:
        sc = _char_script(ch)
        if sc in INDIC_SCRIPTS:
            return True
    return False


def script_ratio_for_language(text: str, language: str) -> float:
    """Fraction of alphabetic characters in `text` matching `language`'s expected primary script."""
    expected = LANGUAGE_PRIMARY_SCRIPT.get(language) or LANGUAGE_PRIMARY_SCRIPT.get(language.split("-")[0])
    if not expected:
        return 1.0
    hist = script_histogram(text)
    if not hist:
        return 0.0
    return float(hist.get(expected, 0.0))


def _tokenize_words(text: str) -> list[str]:
    return [w for w in re.split(r"[\s,.;:!?।॥\"'()\[\]{}|/\\<>-]+", text) if w]


def detect_text_language(text: str) -> dict[str, object]:
    """Classify language of text deterministically via Unicode script + stopword disambiguation."""
    names = language_names()
    hist = script_histogram(text)
    if not hist:
        return {
            "language": None,
            "candidates": [],
            "confidence": 0.0,
            "ambiguous": True,
            "method": "empty_or_non_alpha",
            "script_histogram": hist,
        }

    top_script, top_ratio = next(iter(hist.items()))
    script_langs = next((langs for name, _, _, langs in SCRIPT_RANGES if name == top_script), [])

    # Single-language scripts resolve directly.
    if len(script_langs) == 1:
        code = script_langs[0]
        return {
            "language": code,
            "candidates": [
                {"language": code, "name": names.get(code, code), "confidence": round(top_ratio, 3), "script": top_script}
            ],
            "confidence": round(top_ratio, 3),
            "ambiguous": top_ratio < 0.65,
            "method": "unicode_script",
            "script_histogram": hist,
        }

    # Shared-script disambiguation using stopwords & script-unique characters.
    words = _tokenize_words(text)
    stopwords_table: dict[str, set[str]] = {}
    bonus: dict[str, float] = {}
    if top_script == "Devanagari":
        stopwords_table = DEVANAGARI_STOPWORDS
        # Marathi uses ळ (U+0933) frequently; Hindi rarely does.
        if "ळ" in text:
            bonus["mr"] = 2.5
    elif top_script == "Bengali":
        stopwords_table = BENGALI_STOPWORDS
        # Assamese-distinctive letters ৰ (U+09F0) and ৱ (U+09F1).
        if "ৰ" in text or "ৱ" in text:
            bonus["as"] = 4.0
    elif top_script == "Arabic":
        stopwords_table = ARABIC_STOPWORDS

    scores: dict[str, float] = {}
    for lang, sw in stopwords_table.items():
        hits = sum(1 for w in words if w in sw)
        scores[lang] = float(hits) + bonus.get(lang, 0.0)

    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    total_hits = sum(s for _, s in ranked)

    if total_hits == 0:
        default_lang = script_langs[0]
        candidates = [
            {"language": c, "name": names.get(c, c), "confidence": round(1.0 / min(3, len(script_langs)), 3), "script": top_script}
            for c in script_langs[:3]
        ]
        return {
            "language": default_lang,
            "candidates": candidates,
            "confidence": 0.4,
            "ambiguous": True,
            "method": "unicode_script_shared_no_stopword_match",
            "script_histogram": hist,
        }

    top_lang, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = (top_score - second_score) / max(1.0, top_score)
    conf = round(min(0.98, top_ratio * (0.55 + 0.45 * margin)), 3)
    ambiguous = margin < 0.25 or top_score < 2.0

    candidates = [
        {
            "language": lang,
            "name": names.get(lang, lang),
            "confidence": round((score / total_hits) * top_ratio, 3),
            "script": top_script,
        }
        for lang, score in ranked
        if score > 0
    ]
    return {
        "language": top_lang,
        "candidates": candidates,
        "confidence": conf,
        "ambiguous": ambiguous,
        "method": "unicode_script_plus_stopwords",
        "script_histogram": hist,
    }


def _is_table_line(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and s.count("|") >= 2


def parse_markdown_blocks(text: str) -> list[tuple[str, str]]:
    """Parse Markdown into typed atomic blocks: 'code', 'latex', 'table', 'paragraph'.

    Tables, fenced code blocks, and $$...$$ LaTeX blocks are kept intact as single units.
    """
    lines = text.splitlines()
    blocks: list[tuple[str, str]] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # Fenced code block.
        if stripped.startswith("```"):
            buf = [line]
            i += 1
            while i < n:
                buf.append(lines[i])
                if lines[i].strip().startswith("```"):
                    i += 1
                    break
                i += 1
            blocks.append(("code", "\n".join(buf)))
            continue

        # Block LaTeX $$...$$.
        if stripped.startswith("$$"):
            buf = [line]
            i += 1
            if not (stripped.endswith("$$") and len(stripped) > 2):
                while i < n:
                    buf.append(lines[i])
                    if lines[i].strip().endswith("$$"):
                        i += 1
                        break
                    i += 1
            blocks.append(("latex", "\n".join(buf)))
            continue

        # Markdown table.
        if _is_table_line(line):
            buf = [line]
            i += 1
            while i < n and _is_table_line(lines[i]):
                buf.append(lines[i])
                i += 1
            blocks.append(("table", "\n".join(buf)))
            continue

        # Regular paragraph: collect until blank line or structural block start.
        buf = [line]
        i += 1
        while i < n:
            nxt = lines[i]
            nxt_s = nxt.strip()
            if not nxt_s or nxt_s.startswith("```") or nxt_s.startswith("$$") or _is_table_line(nxt):
                break
            buf.append(nxt)
            i += 1
        blocks.append(("paragraph", "\n".join(buf)))

    return blocks


def chunk_markdown_for_translation(text: str, *, max_chars: int = 1800) -> list[str]:
    """Chunk Markdown text for indic-translate without ever splitting tables, code, or LaTeX blocks."""
    blocks = parse_markdown_blocks(text)
    if not blocks:
        return []

    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0

    def flush() -> None:
        nonlocal cur, cur_len
        if cur:
            chunks.append("\n\n".join(cur))
            cur = []
            cur_len = 0

    for btype, content in blocks:
        if btype in ("table", "code", "latex"):
            # Keep structural blocks isolated or grouped only if they fit comfortably.
            if cur_len + len(content) + 2 > max_chars:
                flush()
            if len(content) > max_chars:
                flush()
                chunks.append(content)
            else:
                cur.append(content)
                cur_len += len(content) + 2
            continue

        # Regular paragraph: split by sentences only if a single paragraph exceeds max_chars.
        if len(content) > max_chars:
            flush()
            for sub in split_text_at_sentences(content, max_chars=max_chars):
                chunks.append(sub)
            continue

        if cur_len + len(content) + 2 > max_chars:
            flush()
        cur.append(content)
        cur_len += len(content) + 2

    flush()
    return chunks


def split_text_at_sentences(text: str, *, max_chars: int = 250) -> list[str]:
    """Split text cleanly along script-aware sentence boundaries so every piece <= max_chars."""
    cleaned = text.strip()
    if not cleaned:
        return []
    raw_sentences = [s.strip() for s in SENTENCE_SPLIT_RE.split(cleaned) if s.strip()]
    pieces: list[str] = []

    for sent in raw_sentences:
        if len(sent) <= max_chars:
            pieces.append(sent)
            continue
        # Sub-split long sentences on clause punctuation, then whitespace.
        sub_parts = [p.strip() for p in re.split(r"(?<=[,;:])\s+", sent) if p.strip()]
        buf = ""
        for part in sub_parts:
            if len(part) > max_chars:
                if buf:
                    pieces.append(buf)
                    buf = ""
                words = part.split()
                wbuf = ""
                for w in words:
                    if wbuf and len(wbuf) + 1 + len(w) > max_chars:
                        pieces.append(wbuf)
                        wbuf = w
                    elif len(w) > max_chars:
                        if wbuf:
                            pieces.append(wbuf)
                            wbuf = ""
                        for start in range(0, len(w), max_chars):
                            pieces.append(w[start : start + max_chars])
                    else:
                        wbuf = f"{wbuf} {w}".strip()
                if wbuf:
                    pieces.append(wbuf)
            elif buf and len(buf) + 1 + len(part) > max_chars:
                pieces.append(buf)
                buf = part
            else:
                buf = f"{buf} {part}".strip()
        if buf:
            pieces.append(buf)

    # Greedily pack short consecutive sentences up to max_chars so we don't make tiny TTS calls.
    packed: list[str] = []
    cur = ""
    for piece in pieces:
        if cur and len(cur) + 1 + len(piece) <= max_chars:
            cur = f"{cur} {piece}"
        else:
            if cur:
                packed.append(cur)
            cur = piece
    if cur:
        packed.append(cur)
    return packed


def split_chunk_in_half(chunk: str) -> tuple[str, str]:
    """Split a chunk in half cleanly (for indic-translate truncated:true recovery).

    For Markdown tables, splits by rows while duplicating the header + separator row
    so both halves remain valid Markdown tables.
    """
    lines = chunk.splitlines()
    if len(lines) >= 4 and all(_is_table_line(ln) for ln in lines):
        # Preserve header (line 0) and separator (line 1).
        header = lines[:2]
        body = lines[2:]
        mid = max(1, len(body) // 2)
        left = "\n".join(header + body[:mid])
        right = "\n".join(header + body[mid:])
        return left, right

    if "\n\n" in chunk:
        parts = chunk.split("\n\n")
        mid = max(1, len(parts) // 2)
        return "\n\n".join(parts[:mid]), "\n\n".join(parts[mid:])

    sentences = [s.strip() for s in SENTENCE_SPLIT_RE.split(chunk) if s.strip()]
    if len(sentences) >= 2:
        mid = len(sentences) // 2
        return " ".join(sentences[:mid]), " ".join(sentences[mid:])

    words = chunk.split()
    if len(words) >= 2:
        mid = len(words) // 2
        return " ".join(words[:mid]), " ".join(words[mid:])

    mid = max(1, len(chunk) // 2)
    return chunk[:mid], chunk[mid:]


def extract_markdown_tables(text: str) -> list[dict[str, int]]:
    """Extract Markdown table shapes (row_count excluding separator, col_count)."""
    tables: list[dict[str, int]] = []
    for btype, content in parse_markdown_blocks(text):
        if btype != "table":
            continue
        lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
        data_rows: list[list[str]] = []
        for ln in lines:
            cells = [c.strip() for c in ln.strip("|").split("|")]
            # Skip separator row like | --- | :---: |
            if all(re.fullmatch(r":?-{2,}:?", c.replace(" ", "")) for c in cells if c):
                continue
            data_rows.append(cells)
        if data_rows:
            tables.append({"rows": len(data_rows), "cols": len(data_rows[0])})
    return tables


_MD_HR_RE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
_MD_SEP_CELL_RE = re.compile(r":?-{2,}:?")


def _strip_inline_markdown(text: str) -> str:
    """Strip Markdown punctuation/markup without altering any Indic or English words."""
    s = re.sub(r"<br\s*/?>", "; ", text, flags=re.IGNORECASE)
    s = re.sub(r"</?[A-Za-z][A-Za-z0-9]*(?:\s+[^>]*)?>", "", s)
    # Images ![alt](url) -> alt, links [label](url) -> label.
    s = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    # Inline code, bold, italic, strikethrough delimiters only.
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"\*\*\*([^*]+)\*\*\*", r"\1", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"\*([^*\n]+)\*", r"\1", s)
    s = re.sub(r"___([^_]+)___", r"\1", s)
    s = re.sub(r"__([^_]+)__", r"\1", s)
    s = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", s)
    s = re.sub(r"~~([^~]+)~~", r"\1", s)
    # Inline pipe separators (e.g. "Total: 86 | Cost: 11100") -> semicolon pause.
    s = re.sub(r"\s*\|\s*", "; ", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


def _ensure_sentence_end(text: str) -> str:
    s = text.strip()
    if not s:
        return ""
    if s[-1] not in ".!?।॥؟":
        return f"{s}."
    return s


def markdown_to_speakable_text(text: str) -> str:
    """Deterministically convert Markdown syntax/tables into natural speakable text.

    Only removes/reformats Markdown punctuation and structure (pipes, separator rows,
    heading hashes, bold/italic markers, list bullets, links). Never translates, writes,
    or rewrites any Indic or English words so byte-identical provenance is preserved.
    """
    blocks = parse_markdown_blocks(text)
    if not blocks:
        return text.strip()

    out_blocks: list[str] = []
    for btype, content in blocks:
        if btype == "table":
            lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
            data_rows: list[list[str]] = []
            for ln in lines:
                cells = [c.strip() for c in ln.strip("|").split("|")]
                non_empty = [c.replace(" ", "") for c in cells if c]
                if non_empty and all(_MD_SEP_CELL_RE.fullmatch(c) for c in non_empty):
                    continue
                data_rows.append([_strip_inline_markdown(c) for c in cells])
            if not data_rows:
                continue
            row_sentences: list[str] = []
            if len(data_rows) >= 2:
                headers = data_rows[0]
                for row in data_rows[1:]:
                    parts: list[str] = []
                    for col_idx, cell in enumerate(row):
                        if not cell:
                            continue
                        hdr = headers[col_idx] if col_idx < len(headers) else ""
                        parts.append(f"{hdr}: {cell}" if hdr else cell)
                    if parts:
                        row_sentences.append(_ensure_sentence_end("; ".join(parts)))
            else:
                parts = [c for c in data_rows[0] if c]
                if parts:
                    row_sentences.append(_ensure_sentence_end("; ".join(parts)))
            if row_sentences:
                out_blocks.append("\n".join(row_sentences))
            continue

        if btype == "code":
            inner_lines = [
                ln.strip()
                for ln in content.splitlines()
                if ln.strip() and not ln.strip().startswith("```")
            ]
            if inner_lines:
                out_blocks.append("\n".join(inner_lines))
            continue

        if btype == "latex":
            inner = content.strip().strip("$").strip()
            if inner:
                out_blocks.append(inner)
            continue

        # Regular paragraph block.
        cleaned_lines: list[str] = []
        for ln in content.splitlines():
            if not ln.strip() or _MD_HR_RE.fullmatch(ln):
                continue
            s = re.sub(r"^\s*(?:>\s*)+", "", ln)
            s = re.sub(r"^\s*#{1,6}\s+", "", s)
            s = re.sub(r"\s+#+\s*$", "", s)
            s = re.sub(r"^\s*[-*+]\s+", "", s)
            s = _strip_inline_markdown(s)
            if s:
                cleaned_lines.append(s)
        if cleaned_lines:
            out_blocks.append("\n".join(cleaned_lines))

    return "\n\n".join(out_blocks).strip()


def extract_digits(text: str) -> list[str]:
    """Extract normalized ASCII numeric sequences from text (handles Indic + ASCII digits)."""
    normalized = text.translate(INDIC_DIGIT_TRANS)
    return re.findall(r"\d+(?:[.,]\d+)?", normalized)


def normalize_for_cer(text: str) -> str:
    """NFKC-normalize, lowercase, strip punctuation, and collapse whitespace for CER comparison."""
    norm = unicodedata.normalize("NFKC", text).lower()
    # Keep letters, combining marks (essential for Indic scripts!), numbers, and spaces.
    kept = []
    for ch in norm:
        cat = unicodedata.category(ch)
        if cat[0] in ("L", "M", "N"):
            kept.append(ch)
        elif ch.isspace():
            kept.append(" ")
    return re.sub(r"\s+", " ", "".join(kept)).strip()


def compute_cer(expected: str, actual: str) -> float:
    """Compute Character Error Rate (Levenshtein distance / len(expected)) on normalized text."""
    ref = normalize_for_cer(expected)
    hyp = normalize_for_cer(actual)
    if not ref:
        return 0.0 if not hyp else 1.0
    if not hyp:
        return 1.0

    # Standard O(min(m, n)) space DP edit distance.
    m, n = len(ref), len(hyp)
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        curr = [i] + [0] * n
        r_ch = ref[i - 1]
        for j in range(1, n + 1):
            cost = 0 if r_ch == hyp[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return round(prev[n] / float(m), 4)


def compute_cer_spoken(expected: str, actual: str) -> float:
    """Compute speech back-transcription CER tolerating ASR spelled-out numbers (missing ITN).

    When `expected` has no digits, this is identical to `compute_cer`.
    When `expected` contains digit sequences (e.g. '250', '1800', '11100'), Indic ASR commonly
    transcribes spoken numbers as words (e.g. Tamil/English words of 6-30 chars) rather than
    digits. Each digit sequence in `expected` may match either the exact digits or a bounded
    non-digit spoken-word span in `actual` (1..min(36, 7*len+8) chars), while still penalizing
    wrong digits, dropped words, wrong scripts, and hallucinations.
    """
    ref = normalize_for_cer(expected).translate(INDIC_DIGIT_TRANS)
    hyp = normalize_for_cer(actual).translate(INDIC_DIGIT_TRANS)
    if not ref:
        return 0.0 if not hyp else 1.0
    if not hyp:
        return 1.0
    if not re.search(r"\d", ref):
        return compute_cer(expected, actual)

    tokens: list[tuple[str, str, int]] = []
    i = 0
    while i < len(ref):
        if ref[i].isdigit():
            j = i
            while j < len(ref) and ref[j].isdigit():
                j += 1
            d_str = ref[i:j]
            tokens.append(("NUM", d_str, min(36, len(d_str) * 7 + 8)))
            i = j
        else:
            tokens.append(("CH", ref[i], 1))
            i += 1

    m, n = len(tokens), len(hyp)
    prev = list(range(n + 1))
    for ti in range(1, m + 1):
        kind, val, max_span = tokens[ti - 1]
        if kind == "CH":
            curr = [prev[0] + 1] + [0] * n
            for j in range(1, n + 1):
                cost = 0 if val == hyp[j - 1] else 1
                curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        else:
            omit_cost = max(2, len(val))
            curr = [prev[0] + omit_cost] + [0] * n
            for j in range(1, n + 1):
                best = prev[j] + omit_cost
                # Exact digit match.
                dlen = len(val)
                if j >= dlen and hyp[j - dlen : j] == val:
                    best = min(best, prev[j - dlen])
                # Spoken-word (non-digit) span of length 1..max_span.
                lo = max(0, j - max_span)
                for k in range(j - 1, lo - 1, -1):
                    if hyp[k].isdigit():
                        break
                    if prev[k] < best:
                        best = prev[k]
                curr[j] = min(best, curr[j - 1] + 1)
        prev = curr

    denom = max(1, len(tokens))
    return round(prev[n] / float(denom), 4)


def detect_repetition(text: str, *, min_ngram: int = 3, min_repeats: int = 3) -> tuple[bool, str | None]:
    """Detect Indic-Speak style repetition loops via repeated word n-grams or consecutive phrases."""
    norm = normalize_for_cer(text)
    words = norm.split()
    if len(words) >= min_ngram * min_repeats:
        counts: dict[tuple[str, ...], int] = {}
        for i in range(len(words) - min_ngram + 1):
            ng = tuple(words[i : i + min_ngram])
            counts[ng] = counts.get(ng, 0) + 1
            if counts[ng] >= min_repeats:
                return True, " ".join(ng)

    # Also catch immediate consecutive repetition of any 2+ word phrase 3 times in a row.
    m = re.search(r"(\b\S+(?:\s+\S+){1,6}\b)(?:\s+\1){2,}", norm)
    if m:
        return True, m.group(1)

    # Also catch long character-level consecutive repeats (handles scripts without spaces).
    m_char = re.search(r"(.{10,40})\1{2,}", norm)
    if m_char:
        return True, m_char.group(1)

    return False, None


@dataclass(frozen=True)
class SubtitleCue:
    index: int
    start_s: float
    end_s: float
    text: str


def _fmt_ts(seconds: float, *, vtt: bool = False) -> str:
    ms_total = max(0, int(round(seconds * 1000)))
    hours = ms_total // 3_600_000
    rem = ms_total % 3_600_000
    minutes = rem // 60_000
    rem %= 60_000
    secs = rem // 1000
    ms = rem % 1000
    sep = "." if vtt else ","
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{sep}{ms:03d}"


def build_subtitle_cues(
    segments: list[dict[str, object]],
    *,
    max_cue_chars: int = 84,
) -> list[SubtitleCue]:
    """Build timed cues from segments, splitting long text proportionally by characters."""
    cues: list[SubtitleCue] = []
    cue_idx = 1
    for seg in segments:
        start_s = float(seg["start_s"])
        end_s = float(seg["end_s"])
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        if end_s <= start_s:
            end_s = start_s + 0.5
        if len(text) <= max_cue_chars:
            cues.append(SubtitleCue(index=cue_idx, start_s=start_s, end_s=end_s, text=text))
            cue_idx += 1
            continue

        parts = split_text_at_sentences(text, max_chars=max_cue_chars)
        total_chars = max(1, sum(len(p) for p in parts))
        span = end_s - start_s
        cursor = start_s
        for idx, part in enumerate(parts):
            frac = len(part) / float(total_chars)
            next_t = end_s if idx == len(parts) - 1 else round(cursor + span * frac, 3)
            if next_t <= cursor:
                next_t = round(cursor + 0.1, 3)
            cues.append(SubtitleCue(index=cue_idx, start_s=cursor, end_s=next_t, text=part))
            cue_idx += 1
            cursor = next_t
    return cues


def format_subtitles(
    segments: list[dict[str, object]],
    *,
    fmt: str = "srt",
    max_cue_chars: int = 84,
) -> tuple[str, int]:
    """Render segments into SRT or WebVTT format and return (content, cue_count)."""
    cues = build_subtitle_cues(segments, max_cue_chars=max_cue_chars)
    is_vtt = fmt.lower() == "vtt"
    lines: list[str] = []
    if is_vtt:
        lines.append("WEBVTT\n")
    for cue in cues:
        ts = f"{_fmt_ts(cue.start_s, vtt=is_vtt)} --> {_fmt_ts(cue.end_s, vtt=is_vtt)}"
        if is_vtt:
            lines.append(f"{ts}\n{cue.text}\n")
        else:
            lines.append(f"{cue.index}\n{ts}\n{cue.text}\n")
    return "\n".join(lines).strip() + ("\n" if cues else ""), len(cues)


SRT_TS_RE = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})$"
)


def validate_subtitles(content: str) -> dict[str, object]:
    """Parse SRT/VTT and verify monotonic non-decreasing timestamps and non-empty cues."""
    blocks = [b.strip() for b in re.split(r"\r?\n\s*\r?\n", content.strip()) if b.strip()]
    if blocks and blocks[0].upper().startswith("WEBVTT"):
        blocks = blocks[1:]
    cues = 0
    prev_end = -1.0
    monotonic = True
    errors: list[str] = []

    for i, blk in enumerate(blocks, start=1):
        lines = [ln.strip() for ln in blk.splitlines() if ln.strip()]
        ts_idx = 1 if len(lines) >= 2 and lines[0].isdigit() else 0
        if ts_idx >= len(lines):
            errors.append(f"Cue {i}: missing timestamp line")
            continue
        m = SRT_TS_RE.match(lines[ts_idx])
        if not m:
            errors.append(f"Cue {i}: invalid timestamp '{lines[ts_idx]}'")
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(g) for g in m.groups())
        start_s = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000.0
        end_s = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000.0
        if end_s < start_s:
            monotonic = False
            errors.append(f"Cue {i}: end ({end_s}) < start ({start_s})")
        if start_s + 1e-3 < prev_end:
            monotonic = False
            errors.append(f"Cue {i}: overlapping/non-monotonic start ({start_s} < {prev_end})")
        text_lines = lines[ts_idx + 1 :]
        if not text_lines:
            errors.append(f"Cue {i}: empty cue text")
        prev_end = end_s
        cues += 1

    return {"valid": len(errors) == 0 and cues > 0, "cue_count": cues, "monotonic": monotonic, "errors": errors}
