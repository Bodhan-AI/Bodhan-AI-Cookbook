"""Deterministic translation checks and Unicode-safe evidence coordinates.

Every finding carries an evidence strength that is placed on the calibration
ladder (0.58 / 0.65 / 0.72 / 0.84 / 0.92) by how much the check can verify:

* about 0.9 and above: a literal, non-translatable element (digits, Markdown
  syntax, LaTeX, URLs, script) is provably different between source and
  candidate and the damage is located in the candidate;
* 0.72 to 0.84: the evidence is real but the pairing or the cause is
  ambiguous (for example several numbers differ and some are spelled out);
* below 0.72: plausible but unverifiable (a number that may be spelled out,
  Latin text that may be a valid romanization or kept term). These stay
  visible as candidates, and the calibration decides whether they help.
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from functools import lru_cache

from markdown_it import MarkdownIt
from sacrebleu.metrics import CHRF

from .schema import Finding, Span

MARKDOWN = MarkdownIt("commonmark").enable(["table", "strikethrough"])
CHRF_PLUS_PLUS = CHRF(word_order=2)
NUMBER = re.compile(
    r"(?<!\w)(?P<value>[+\-−]?\d+(?:[.,:/٫٬]\d+)*)(?:\s?(?P<unit>°[CF]|[%₹]|km|cm|mm|kg|mg|mL|Hz|kWh|m/s|s|m|g|L)(?!\w))?",
    re.IGNORECASE,
)
# Inline ``$...$`` follows the Pandoc rule: the opening ``$`` is followed by a
# non-space, the closing ``$`` is preceded by a non-space and not followed by a
# digit. That keeps currency such as "$5 and $10" out of the LaTeX check.
FORMULA = re.compile(
    r"(?<![\\$])(\$\$[\s\S]+?\$\$"
    r"|\$(?![\s$])(?:\\.|[^$\n\\])+?(?<![\s\\])\$(?!\d)"
    r"|\\\[[\s\S]+?\\\]|\\\([^\n]+?\\\)"
    r"|\\begin\{(?:equation|align|gather|multline|eqnarray|math|displaymath)\*?\}[\s\S]+?"
    r"\\end\{(?:equation|align|gather|multline|eqnarray|math|displaymath)\*?\})"
)
ENTITY = re.compile(r"https?://[^\s)>]+|(?<!\w)[A-Z][A-Z0-9]{1,}(?!\w)")
QUANTITY_LABEL = re.compile(r"(?im)^[ \t]*(?P<label>[\w][\w -]*?)[ \t]*:[ \t]*(?P<value>[^\n\r]+)")
UNIT_OBJECT = re.compile(r"\s+of\s+([\w-]+(?:[ \t]+[\w-]+)*?)(?=\s+(?:and|or|with|then|plus)\b|[.,;:\n|!?)]|$)", re.IGNORECASE)
TABLE_PIPE = re.compile(r"(?<!\\)\|")
FENCE = re.compile(r"(?m)^[ \t]*(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:^[ \t]*\1[ \t]*$|\Z)")
INLINE_CODE = re.compile(r"(`+)(?:[^`\n]|[^`\n][^\n]*?[^`\n])\1(?!`)")
URL = re.compile(r"https?://[^\s)>\]|]+|www\.[^\s)>\]|]+")
LINK = re.compile(r"(!?)\[((?:[^\[\]\n]|\[[^\]\n]*\])*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"\n]*\")?\s*\)")
LINK_TARGET = re.compile(r"\]\([^)\n]*\)")
HTML_OR_EMAIL = re.compile(r"</?[A-Za-z][^>\n]*>|[\w.+-]+@[\w-]+\.[\w.-]+")
LATIN_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")
SENTENCE = re.compile(r"[^\n.!?।॥۔]+[.!?।॥۔]*")
INLINE_DELIMITERS = ("**", "__", "~~")
TEXT_COMMAND = re.compile(r"\\(?:text|textrm|textit|textbf|textsf|texttt|textnormal|mathrm|mbox)\s*\{")
SCRIPTS = {
    "as": "BENGALI",
    "bn": "BENGALI",
    "en": "LATIN",
    "gu": "GUJARATI",
    "hi": "DEVANAGARI",
    "kn": "KANNADA",
    "ml": "MALAYALAM",
    "mr": "DEVANAGARI",
    "or": "ORIYA",
    "pa": "GURMUKHI",
    "ta": "TAMIL",
    "te": "TELUGU",
    "ur": "ARABIC",
}
KNOWN_SCRIPTS = frozenset(SCRIPTS.values())

# Scale words, as a units table: power of ten -> common written forms across
# the 13 production languages (English, then as/bn, gu, hi/mr, kn, ml, or, pa,
# ta, te, ur). Native forms match as NFC stems so inflections (Tamil லட்சத்து,
# Telugu కోట్ల) still match. Gaps in this list are covered by a softer
# power-of-ten fallback in ``number_findings``.
SCALE_WORDS = {
    3: (
        "thousand",
        "হাজার", "হাজাৰ", "હજાર", "हज़ार", "हजार", "ಸಾವಿರ", "ആയിര", "ହଜାର", "ਹਜ਼ਾਰ", "ਹਜਾਰ",
        "ஆயிர", "వేల", "వేయి", "ہزار", "هزار",
    ),
    5: (
        "lakh", "lac",
        "লাখ", "লক্ষ", "લાખ", "लाख", "ಲಕ್ಷ", "ലക്ഷ", "ଲକ୍ଷ", "ਲੱਖ", "ਲਖ", "லட்ச", "இலட்ச", "లక్ష", "لاکھ",
    ),
    6: (
        "million",
        "মিলিয়ন", "মিলিয়ন", "મિલિયન", "मिलियन", "ಮಿಲಿಯನ್", "ദശലക്ഷ", "മില്യൺ", "ମିଲିୟନ", "ਮਿਲੀਅਨ",
        "மில்லியன்", "మిలియన్", "ملین",
    ),
    7: (
        "crore",
        "কোটি", "કરોડ", "करोड़", "करोड", "कोटी", "ಕೋಟಿ", "കോടി", "କୋଟି", "ਕਰੋੜ", "கோடி", "కోటి", "కోట్ల", "کروڑ",
    ),
    9: (
        "billion",
        "বিলিয়ন", "বিলিয়ন", "અબજ", "બિલિયન", "अरब", "अब्ज", "बिलियन", "ಬಿಲಿಯನ್", "ശതകോടി", "ബില്യൺ",
        "ବିଲିୟନ", "ਅਰਬ", "ਬਿਲੀਅਨ", "பில்லியன்", "బిలియన్", "ارب", "بلین",
    ),
    12: ("trillion",),
}
SCALE_POWERS = frozenset(SCALE_WORDS)


def _scale_stem(word: str) -> str:
    """Fold a lexicon form and drop a final virama so inflected forms share the stem."""
    folded = unicodedata.normalize("NFC", word).casefold()
    return folded[:-1] if folded and _is_virama(folded[-1]) else folded


def _is_virama(char: str) -> bool:
    """True for the vowel-killer sign of any Indic script."""
    return "VIRAMA" in unicodedata.name(char, "")


LATIN_SCALES = {word: power for power, words in SCALE_WORDS.items() for word in words if word.isascii()}
NATIVE_SCALES = sorted(
    ((_scale_stem(word), power) for power, words in SCALE_WORDS.items() for word in words if not word.isascii()),
    key=lambda entry: -len(entry[0]),
)


LIST_MARKER = re.compile(r"(?m)^([ \t]*)(\d{1,9})([.)])(?=[ \t])")


def ascii_list_markers(text: str) -> str:
    """Read native-digit list numbers ("১.", "२)") as list markers, keeping every offset.

    CommonMark only recognises ASCII digits in ordered-list markers. Localizing the digits
    is a legitimate rendering choice, not a broken list, so they compare as equivalent.
    """
    return LIST_MARKER.sub(lambda m: m.group(1) + "".join(str(unicodedata.decimal(char)) for char in m.group(2)) + m.group(3), text)


@lru_cache(maxsize=64)
def parse_markdown(text: str):
    """Parse once per text; several checks inspect the same token stream."""
    return MARKDOWN.parse(ascii_list_markers(text))


def line_starts(text: str) -> list[int]:
    """Offsets where each line begins, for O(log n) line-to-offset conversion."""
    return [0, *(match.end() for match in re.finditer(r"\n", text))]


def normalize_with_offsets(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Normalize text for ASR alignment while retaining offsets into the original."""
    normalized, positions = [], []
    index = 0
    while index < len(text):
        start = index
        index += 1
        while index < len(text) and unicodedata.category(text[index]).startswith("M"):
            index += 1
        for char in unicodedata.normalize("NFKC", text[start:index]).casefold():
            category = unicodedata.category(char)[0]
            if category in "LMN":
                normalized.append(str(unicodedata.decimal(char)) if char.isdecimal() else char)
                positions.append((start, index))
            elif normalized and normalized[-1] != " ":
                normalized.append(" ")
                positions.append((start, index))
    if normalized and normalized[-1] == " ":
        normalized.pop()
        positions.pop()
    return "".join(normalized), positions


# Western (250,000) and Indian (2,50,000) digit grouping. Other comma uses,
# such as a decimal comma, are kept so they cannot silently collapse.
GROUPED = re.compile(r"(?<![\d,])(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})+,\d{3})(?![\d,])")
PLAIN_DECIMAL = re.compile(r"[+-]?\d+(?:\.\d+)?")


def number_key(text: str) -> str:
    """Normalize script digits and Western or Indian comma grouping, retaining units."""
    folded = unicodedata.normalize("NFKC", text).replace("−", "-").replace("٫", ".").replace("٬", ",")
    folded = "".join(str(unicodedata.decimal(char)) if char.isdecimal() else char for char in folded)
    folded = "".join(folded.split())
    return GROUPED.sub(lambda match: match.group().replace(",", ""), folded).casefold()


@dataclass(eq=False)
class Quantity:
    """One numeric value with its optional unit or scale word, shaped like a regex match."""

    text: str = field(repr=False)
    begin: int
    finish: int
    value: str
    unit: str | None
    scale: int = 0

    def start(self) -> int:
        """Start offset, as on a regex match."""
        return self.begin

    def end(self) -> int:
        """End offset, including any unit or scale word."""
        return self.finish

    def span(self) -> tuple[int, int]:
        """Start and end offsets."""
        return self.begin, self.finish

    def group(self) -> str:
        """The original text of the quantity."""
        return self.text[self.begin : self.finish]

    def __getitem__(self, name: str) -> str | None:
        """Expose 'value' and 'unit' like named regex groups."""
        return {"value": self.value, "unit": self.unit}[name]


def following_word(text: str, index: int) -> tuple[str, int] | None:
    """Return the letter-and-mark word right after ``index`` (one or two spaces allowed)."""
    position, limit = index, min(len(text), index + 2)
    while position < limit and text[position] in " \u00a0\u202f\t":
        position += 1
    end = position
    while end < len(text) and unicodedata.category(text[end])[0] in "LM":
        end += 1
    return (text[position:end], end) if end > position else None


def scale_word(text: str, index: int) -> tuple[int, int] | None:
    """Recognize a scale word (thousand, lakh, million, crore, billion) after a number."""
    found = following_word(text, index)
    if found is None:
        return None
    word, end = found
    folded = unicodedata.normalize("NFC", word).casefold()
    if folded.isascii():
        power = LATIN_SCALES.get(folded) or LATIN_SCALES.get(folded[:-1] if folded.endswith("s") else "")
        return (power, end) if power else None
    for stem, power in NATIVE_SCALES:
        if folded.startswith(stem):
            rest = folded[len(stem) :]
            # A virama joining another letter makes a different word (লক্ষ্য "target").
            if len(rest) >= 2 and _is_virama(rest[0]) and unicodedata.category(rest[1])[0] == "L":
                continue
            return power, end
    return None


def quantities(text: str) -> list[Quantity]:
    """Find numbers, splitting times, dates and ratios into integer components."""
    found = []
    for match in NUMBER.finditer(text):
        value, unit = match["value"], match["unit"]
        offset = match.start("value")
        if re.search(r"[:/]", value):
            parts = list(re.finditer(r"[+\-−]?\d+(?:[.,٫٬]\d+)*", value))
            for index, part in enumerate(parts):
                last = index == len(parts) - 1
                end = match.end() if last else offset + part.end()
                found.append(Quantity(text, offset + part.start(), end, part.group(), unit if last else None))
            continue
        begin = match.start()
        # Python's \w excludes combining marks, so the hyphen in "கோவிட்-19" or
        # "जी-20" passes the lookbehind; a hyphen joined to a word is not a sign.
        if value[0] in "+-−" and begin and unicodedata.category(text[begin - 1])[0] in "LMN":
            begin, value = begin + 1, value[1:]
        scale, end = 0, match.end()
        if not unit and (scaled := scale_word(text, match.end("value"))):
            scale, end = scaled
        found.append(Quantity(text, begin, end, value, unit, scale))
    return found


def unmatched(
    expected: Iterable,
    candidate: Iterable,
    key: Callable[[object], str],
) -> list:
    """Find expected occurrences left over after multiset matching."""
    actual = Counter(map(key, candidate))
    missing = []
    for match in expected:
        norm = key(match)
        if actual[norm]:
            actual[norm] -= 1
        else:
            missing.append(match)
    return missing


def decimal_value(match) -> Decimal | None:
    """Exact value after digit, grouping and scale normalization, if it is a plain number."""
    base = number_key(match["value"])
    if not PLAIN_DECIMAL.fullmatch(base):
        return None
    try:
        return Decimal(base).scaleb(getattr(match, "scale", 0))
    except InvalidOperation:
        return None


def numeric_value(match) -> str:
    """Compare the numeric value separately from the unit's written form."""
    value = decimal_value(match)
    if value is not None:
        return format(value.normalize(), "f") if value else "0"
    scale = getattr(match, "scale", 0)
    return number_key(match["value"]) + (f"e{scale}" if scale else "")


def context_key(text: str) -> str:
    """Normalize a literal label for matching without using word order or translation guesses."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def row_cells(raw: str) -> list[tuple[int, int]]:
    """Split one raw table row on unescaped pipes, dropping the outer empty edges."""
    cuts = [match.start() for match in TABLE_PIPE.finditer(raw)]
    boundaries = [-1, *cuts, len(raw)]
    cells = [(start + 1, end) for start, end in zip(boundaries, boundaries[1:], strict=False)]
    if cells and not raw[slice(*cells[0])].strip():
        cells = cells[1:]
    if cells and not raw[slice(*cells[-1])].strip():
        cells = cells[:-1]
    return cells


def table_rows(text: str) -> list[list[tuple[int, int, list[tuple[int, int]], bool]]]:
    """Return each table's rows as (start, end, absolute cell spans, is_header)."""
    if "|" not in text:
        return []
    starts = line_starts(text)
    tables, heading = [], False
    for token in parse_markdown(text):
        if token.type == "table_open":
            tables.append([])
        elif token.type == "thead_open":
            heading = True
        elif token.type == "thead_close":
            heading = False
        elif token.type == "tr_open" and token.map and tables:
            row = line_span(text, tuple(token.map), "source", starts)
            raw = text[row.start : row.end].rstrip()
            cells = [(row.start + start, row.start + end) for start, end in row_cells(raw)]
            tables[-1].append((row.start, row.start + len(raw), cells, heading))
    return tables


def table_quantity_contexts(text: str, numbers: list[Quantity] | None = None):
    """Yield quantities in Markdown table cells with explicit row and column labels."""
    numbers = quantities(text) if numbers is None else numbers
    positions = [number.start() for number in numbers]
    for rows in table_rows(text):
        columns = ()
        for _, _, cells, heading in rows:
            labels = tuple(context_key(text[start:end]) for start, end in cells)
            if heading:
                columns = labels if len(set(labels)) == len(labels) and all(labels) else ()
            elif len(labels) > 1 and len(labels) == len(columns) and labels[0]:
                for column, (start, end) in zip(columns[1:], cells[1:], strict=True):
                    inside = numbers[bisect.bisect_left(positions, start) : bisect.bisect_left(positions, end)]
                    if len(inside) == 1:
                        yield ("table", labels[0], column), inside[0]


def contextual_quantities(text: str, numbers: list[Quantity]) -> dict[tuple[str, ...], Quantity]:
    """Index only unambiguous quantities attached to a shared literal label or table cell."""
    labels = defaultdict(list)
    positions = [number.start() for number in numbers]
    for key, match in table_quantity_contexts(text, numbers):
        labels[key].append(match)
    for label in QUANTITY_LABEL.finditer(text):
        start, end = label.span("value")
        inside = numbers[bisect.bisect_left(positions, start) : bisect.bisect_left(positions, end)]
        if len(inside) == 1:
            labels[("field", context_key(label["label"]))].append(inside[0])
    for number in numbers:
        if number["unit"] and (label := UNIT_OBJECT.match(text, number.end())):
            labels[("of", context_key(label[1]))].append(number)
    return {key: matches[0] for key, matches in labels.items() if len(matches) == 1}


def integer_digits(match) -> int:
    """Number of integer digits in the written value, ignoring grouping and sign."""
    return len(re.split(r"[.]", number_key(match["value"]).lstrip("+-"))[0])


def scale_conversion_suspect(before, after, candidate: str) -> bool:
    """A pure power-of-ten ratio next to a word is likely a scale word this table lacks."""
    first, second = decimal_value(before), decimal_value(after)
    if not first or not second:
        return False
    ratio = max(abs(first), abs(second)) / min(abs(first), abs(second))
    power = ratio.log10() if ratio > 0 else Decimal(0)
    if power != power.to_integral_value() or int(power) <= 0:
        return False
    plausible = getattr(before, "scale", 0) or getattr(after, "scale", 0) or int(power) in SCALE_POWERS
    return bool(plausible) and following_word(candidate, after.end()) is not None and not getattr(after, "scale", 0)


def number_findings(source: str, candidate: str, field: str) -> list[Finding]:
    """Localize changed values on either side and defer ambiguous spelled-out numbers."""
    actual = quantities(candidate)
    if not actual:
        return []
    original = quantities(source)
    source_contexts = contextual_quantities(source, original)
    actual_contexts = contextual_quantities(candidate, actual)
    used_source, used_actual = set(), set()
    findings = []
    for key, before in source_contexts.items():
        after = actual_contexts.get(key)
        if after is None or before.start() in used_source or after.start() in used_actual:
            continue
        used_source.add(before.start())
        used_actual.add(after.start())
        units_differ = before["unit"] and after["unit"] and quantity_key(before) != quantity_key(after)
        if numeric_value(before) != numeric_value(after) or units_differ:
            findings.append(
                Finding(
                    "number",
                    "major",
                    Span("translation", *after.span()),
                    "numbers",
                    after.group(),
                    f"Quantity changed for {' / '.join(key[1:])!r}. Expected {before.group()!r}.",
                    0.88,
                    Span(field, *before.span()),
                )
            )
    # Keep the global check for unlabeled or translated contexts. Equal residual
    # counts pair by position; otherwise only same-magnitude values pair, because
    # the surplus means a value was spelled out or added and position is unreliable.
    original = [match for match in original if match.start() not in used_source]
    actual = [match for match in actual if match.start() not in used_actual]
    missing = unmatched(original, actual, numeric_value)
    extra = unmatched(actual, original, numeric_value)
    if len(missing) == len(extra):
        paired, strength = list(zip(missing, extra, strict=True)), 0.88
    else:
        paired, strength, cursor = [], 0.78, 0
        for after in extra:
            for index in range(cursor, len(missing)):
                if integer_digits(missing[index]) == integer_digits(after):
                    paired.append((missing[index], after))
                    cursor = index + 1
                    break
    for before, after in paired:
        suspect = scale_conversion_suspect(before, after, candidate)
        findings.append(
            Finding(
                "number",
                "major",
                Span("translation", after.start(), after.end()),
                "numbers",
                after.group(),
                (
                    f"Value differs from {before.group()!r} by a power of ten next to a word; possibly a scale word "
                    "(lakh, crore, million) the checker does not know."
                    if suspect
                    else f"Unmatched candidate quantity. Comparison against {field} found {before.group()!r}."
                ),
                0.6 if suspect else strength,
                Span(field, *before.span()),
            )
        )
    paired_before = {id(before) for before, _ in paired}
    paired_after = {id(after) for _, after in paired}
    for match in missing:
        if id(match) not in paired_before:
            # Usually a spelled-out number ("दो" for 2). Unverifiable, so informational.
            findings.append(
                Finding(
                    "number",
                    "minor",
                    Span(field, match.start(), match.end()),
                    "numbers",
                    match.group(),
                    "No matching numeric form in the candidate. Check if the value is spelled out.",
                    0.55,
                )
            )
    for match in extra:
        if id(match) not in paired_after:
            findings.append(
                Finding(
                    "number",
                    "major",
                    Span("translation", match.start(), match.end()),
                    "numbers",
                    match.group(),
                    "No matching numeric form in the comparison text.",
                    0.62,
                )
            )
    # Check units only when both sides use explicit, recognized symbols.
    # A localized unit name will otherwise look like a missing symbol.
    original_units = [match for match in original if match["unit"]]
    actual_units = [match for match in actual if match["unit"]]
    for after in unmatched(actual_units, original_units, quantity_key):
        same_value = [match for match in original_units if numeric_value(match) == numeric_value(after)]
        expected = {number_key(match["unit"]) for match in same_value}
        if expected and number_key(after["unit"]) not in expected:
            findings.append(
                Finding(
                    "number",
                    "major",
                    Span("translation", *after.span()),
                    "numbers",
                    after.group(),
                    f"The written unit differs for the same value. Expected one of {', '.join(sorted(expected))}.",
                    0.88,
                    Span(field, *same_value[0].span()) if len(same_value) == 1 else None,
                )
            )
    return findings


def quantity_key(match) -> str:
    """Combine one numeric value and its explicit symbol for exact matching."""
    return numeric_value(match) + " " + number_key(match["unit"])


def markdown_shape(text: str) -> list[tuple[str, str, tuple[int, int] | None]]:
    """Capture structural tokens with original line ranges, ignoring prose and formatting style."""
    block_types = {
        "heading_open",
        "bullet_list_open",
        "ordered_list_open",
        "list_item_open",
        "table_open",
        "tr_open",
        "th_open",
        "td_open",
        "fence",
    }
    shape = []
    for token in parse_markdown(text):
        if token.type in block_types:
            shape.append((token.type, token.tag, tuple(token.map) if token.map else None))
    return shape


def line_span(text: str, lines: tuple[int, int] | None, field: str, starts: list[int] | None = None) -> Span:
    """Convert a Markdown token's line range to an evidence span."""
    if lines is None:
        return Span(field, 0, len(text))
    starts = line_starts(text) if starts is None else starts
    start, end = lines

    def offset(line: int) -> int:
        """Offset of a line start, or the text end past the last line."""
        return starts[line] if line < len(starts) else len(text)

    return Span(field, offset(start), offset(end))


def trimmed(text: str, start: int, end: int, field: str) -> Span:
    """Shrink a span to exclude surrounding whitespace, keeping it non-empty when possible."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return Span(field, start, end)


def strip_text_arguments(body: str) -> str:
    """Empty the arguments of \\text{}/\\mathrm{}-style commands, which may be translated."""
    out, index = [], 0
    while (match := TEXT_COMMAND.search(body, index)) is not None:
        out.append(body[index : match.end()])
        depth, position = 1, match.end()
        while position < len(body) and depth:
            char = body[position]
            if char == "\\":
                position += 2
                continue
            depth += {"{": 1, "}": -1}.get(char, 0)
            position += 1
        out.append("}")
        index = position
    out.append(body[index:])
    return "".join(out)


def formula_body(value: str) -> str:
    """Remove the math delimiters from one FORMULA match."""
    for opening, closing in (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$")):
        if value.startswith(opening) and value.endswith(closing) and len(value) >= len(opening) + len(closing):
            return value[len(opening) : len(value) - len(closing)]
    return value


def formula_key(match) -> str:
    """Compare the LaTeX skeleton: no whitespace, delimiters, or \\text{} arguments."""
    return "".join(strip_text_arguments(formula_body(match.group())).split())


def mask(text: str, patterns: Iterable[re.Pattern[str]]) -> str:
    """Blank matched regions with spaces (newlines kept) so offsets stay valid."""
    chars = list(text)
    for pattern in patterns:
        for match in pattern.finditer(text):
            for index in range(*match.span()):
                if chars[index] != "\n":
                    chars[index] = " "
    return "".join(chars)


def pipe_block(text: str) -> Span | None:
    """Locate the first run of lines that look like table rows (two or more pipes)."""
    starts = line_starts(text)
    lines = text.split("\n")
    rows = [index for index, line in enumerate(lines) if len(TABLE_PIPE.findall(line)) >= 2]
    if not rows:
        return None
    first = last = rows[0]
    while last + 1 in rows:
        last += 1
    return trimmed(text, starts[first], starts[last] + len(lines[last]), "translation")


def structure_finding(source: str, candidate: str) -> Finding | None:
    """Flag changed Markdown topology, anchored in the candidate where possible."""
    original, translated = markdown_shape(source), markdown_shape(candidate)
    if [entry[:2] for entry in original] == [entry[:2] for entry in translated]:
        return None
    mismatch = next(
        (i for i, (a, b) in enumerate(zip(original, translated, strict=False)) if a[:2] != b[:2]),
        min(len(original), len(translated)),
    )
    anchor = None
    if mismatch < len(original):
        lines = next((entry[2] for entry in reversed(original[: mismatch + 1]) if entry[2]), None)
        anchor = line_span(source, lines, "source")
    span = None
    if mismatch < len(translated):
        lines = next((entry[2] for entry in reversed(translated[: mismatch + 1]) if entry[2]), None)
        span = line_span(candidate, lines, "translation")
    elif mismatch < len(original) and original[mismatch][0] in ("table_open", "tr_open", "th_open", "td_open"):
        span = pipe_block(candidate)
    if span is None and candidate.strip():
        lines = next((entry[2] for entry in reversed(translated) if entry[2]), None)
        span = line_span(candidate, lines, "translation") if lines else trimmed(candidate, 0, len(candidate), "translation")
    if span is None:
        span = anchor or Span("source", 0, len(source))
        anchor = None
    span = trimmed(candidate if span.field == "translation" else source, span.start, span.end, span.field) if span.end > span.start else span
    value = candidate if span.field == "translation" else source
    return Finding(
        "format",
        "major",
        span,
        "markdown",
        value[span.start : span.end],
        "Markdown headings, list items, code fences, or table shape changed.",
        0.98,
        anchor,
    )


def table_findings(source: str, candidate: str) -> list[Finding]:
    """Compare raw cell counts and emptied cells row by row (GFM pads short rows silently)."""
    findings = []
    for source_rows, candidate_rows in zip(table_rows(source), table_rows(candidate), strict=False):
        for before, after in zip(source_rows, candidate_rows, strict=False):
            source_cells = [source[start:end].strip() for start, end in before[2]]
            candidate_cells = [candidate[start:end].strip() for start, end in after[2]]
            if len(source_cells) != len(candidate_cells):
                detail = f"Table row has {len(candidate_cells)} cells; the source row has {len(source_cells)} (merged or dropped cell)."
            elif any(old and not new for old, new in zip(source_cells, candidate_cells, strict=True)):
                detail = "A table cell that has content in the source is empty in the candidate."
            else:
                continue
            span = trimmed(candidate, after[0], after[1], "translation")
            findings.append(
                Finding(
                    "format",
                    "major",
                    span,
                    "markdown",
                    candidate[span.start : span.end],
                    detail,
                    0.92,
                    Span("source", before[0], before[1]),
                )
            )
    return findings


def delimiter_positions(line: str, delimiter: str) -> list[int]:
    """Positions of runs of exactly this two-character delimiter.

    Longer runs (fill-in blanks "____", bold-italic "***") are excluded.
    """
    char = re.escape(delimiter[0])
    return [match.start() for match in re.finditer(f"(?<!{char}){char}{{2}}(?!{char})", line)]


def inline_findings(source: str, candidate: str) -> list[Finding]:
    """Detect stray emphasis/code delimiters and broken link syntax in the candidate."""
    findings = []
    code_free = (FENCE, INLINE_CODE, FORMULA, URL)
    masked_source, masked_candidate = mask(source, code_free), mask(candidate, code_free)
    starts = line_starts(candidate)
    candidate_lines = masked_candidate.split("\n")

    def stray(delimiter: str, positions_in_line: Callable[[str], list[int]], name: str) -> None:
        """Report each candidate line holding an odd number of this delimiter."""
        for number, line in enumerate(candidate_lines):
            positions = positions_in_line(line)
            if len(positions) % 2 == 0:
                continue
            # Which delimiter is literal depends on CommonMark's pairing (and on
            # whether the opener or closer was removed), so report the whole line.
            begin = len(line) - len(line.lstrip(" \t>-+#0123456789.|"))
            span = trimmed(candidate, starts[number] + min(begin, positions[0]), starts[number] + len(line), "translation")
            if span.end > span.start:
                findings.append(
                    Finding(
                        "format",
                        "major",
                        span,
                        "markdown",
                        candidate[span.start : span.end],
                        f"Unbalanced {name} delimiter {delimiter!r}: the markup renders as literal text.",
                        0.9,
                    )
                )

    for delimiter in INLINE_DELIMITERS:
        before = len(delimiter_positions(masked_source, delimiter))
        after = len(delimiter_positions(masked_candidate, delimiter))
        if before == after:
            continue
        if before % 2 == 0 and after % 2 == 1:
            stray(delimiter, lambda line, d=delimiter: delimiter_positions(line, d), "emphasis")
        elif after < before:
            span = trimmed(candidate, 0, len(candidate), "translation")
            findings.append(
                Finding(
                    "format",
                    "minor",
                    span,
                    "markdown",
                    candidate[span.start : span.end],
                    f"The candidate has fewer {delimiter!r} emphasis markers than the source.",
                    0.6,
                )
            )
    fence_free = (FENCE, FORMULA, URL)
    source_ticks = re.findall(r"`+", mask(source, fence_free))
    candidate_masked = mask(candidate, fence_free)
    candidate_ticks = re.findall(r"`+", candidate_masked)
    if len(source_ticks) % 2 == 0 and len(candidate_ticks) % 2 == 1:
        candidate_lines = candidate_masked.split("\n")
        stray("`", lambda line: [match.start() for match in re.finditer(r"`+", line)], "inline code")
    findings.extend(link_findings(source, candidate))
    return findings


def link_findings(source: str, candidate: str) -> list[Finding]:
    """A source link whose URL survives outside valid ``[text](url)`` syntax is broken markup.

    A changed http(s) URL is reported once, by ``entity_findings``; relative link
    targets are not entities, so a changed one is reported here.
    """
    findings = []
    code_free = (FENCE, INLINE_CODE)
    source_links = list(LINK.finditer(mask(source, code_free)))
    if not source_links:
        return findings
    candidate_links = list(LINK.finditer(mask(candidate, code_free)))
    inside = [match.span() for match in candidate_links]
    missing = unmatched(source_links, candidate_links, lambda match: match[3])
    extra = unmatched(candidate_links, source_links, lambda match: match[3])
    relative_missing = []
    for link in missing:
        url = link[3]
        position = candidate.find(url)
        while position >= 0 and any(start <= position < end for start, end in inside):
            position = candidate.find(url, position + 1)
        if position < 0:
            if not url.startswith(("http://", "https://")):
                relative_missing.append(link)
            continue
        line_begin = candidate.rfind("\n", 0, position) + 1
        bracket = candidate.rfind("[", line_begin, position)
        start = bracket if bracket >= 0 else position
        end = position + len(url)
        if end < len(candidate) and candidate[end] == ")":
            end += 1
        span = trimmed(candidate, start, end, "translation")
        findings.append(
            Finding(
                "format",
                "major",
                span,
                "markdown",
                candidate[span.start : span.end],
                "Link syntax is broken: the source link target is present but not in [text](url) form.",
                0.9,
                Span("source", *link.span()),
            )
        )
    relative_extra = [link for link in extra if not link[3].startswith(("http://", "https://"))]
    for before, after in zip(relative_missing, relative_extra, strict=False):
        findings.append(
            Finding(
                "format",
                "major",
                Span("translation", *after.span()),
                "markdown",
                after.group(),
                f"Link target changed from {before[3]!r}.",
                0.88,
                Span("source", *before.span()),
            )
        )
    return findings


def locate_formula(candidate: str, body: str) -> tuple[int, int] | None:
    """Find a formula body in the candidate, ignoring whitespace, and widen to stray delimiters."""
    compact = "".join(body.split())
    if len(compact) < 3 or len(compact) > 400:
        return None
    found = re.search(r"\s*".join(map(re.escape, compact)), candidate)
    if found is None:
        return None
    start, end = found.span()
    while start > 0 and candidate[start - 1] in "$(\\[":
        start -= 1
    while end < len(candidate) and candidate[end] in "$)\\]":
        end += 1
    return start, end


def latex_findings(source: str, candidate: str) -> list[Finding]:
    """Flag missing or altered LaTeX, pointing at the candidate formula when it can be found."""
    findings = []
    missing = unmatched(FORMULA.finditer(source), FORMULA.finditer(candidate), formula_key)
    if not missing:
        return findings
    extra = unmatched(FORMULA.finditer(candidate), FORMULA.finditer(source), formula_key)
    pairs: dict[int, re.Match[str]] = {}
    if len(missing) == len(extra):
        pairs = dict(enumerate(extra))
    else:
        free = list(extra)
        for index, before in enumerate(missing):
            scored = [(SequenceMatcher(None, formula_key(before), formula_key(after)).ratio(), k) for k, after in enumerate(free)]
            if scored and (best := max(scored))[0] >= 0.6:
                pairs[index] = free.pop(best[1])
    for index, before in enumerate(missing):
        anchor = Span("source", before.start(), before.end())
        if index in pairs:
            after = pairs[index]
            findings.append(
                Finding(
                    "format",
                    "major",
                    Span("translation", after.start(), after.end()),
                    "latex",
                    after.group(),
                    f"The LaTeX expression differs from the source {before.group()!r}.",
                    0.98,
                    anchor,
                )
            )
        elif located := locate_formula(candidate, formula_body(before.group())):
            findings.append(
                Finding(
                    "format",
                    "major",
                    Span("translation", *located),
                    "latex",
                    candidate[located[0] : located[1]],
                    f"The math delimiters of {before.group()!r} are broken or missing.",
                    0.98,
                    anchor,
                )
            )
        else:
            findings.append(
                Finding(
                    "format",
                    "major",
                    anchor,
                    "latex",
                    before.group(),
                    "The original equation is missing or its LaTeX content was altered.",
                    0.9,
                )
            )
    return findings


def format_findings(source: str, candidate: str) -> list[Finding]:
    """Flag changed Markdown topology, table cells, inline markup, or LaTeX."""
    findings = []
    if (structure := structure_finding(source, candidate)) is not None:
        findings.append(structure)
    findings.extend(table_findings(source, candidate))
    findings.extend(inline_findings(source, candidate))
    findings.extend(latex_findings(source, candidate))
    return findings


def entity_span(match: re.Match[str]) -> tuple[int, int]:
    """Exclude sentence-final punctuation from a URL span."""
    value = match.group()
    return match.start(), match.start() + len(literal_entity(match)) if value.startswith("http") else match.end()


def entity_findings(source: str, candidate: str, field: str) -> list[Finding]:
    """Detect absent literal identifiers while keeping localizable acronyms as soft evidence.

    A missing URL paired with a new candidate URL is a changed identifier and is
    reported on the candidate. An absent acronym may be a valid transliteration
    and stays soft.
    """
    missing = unmatched(ENTITY.finditer(source), ENTITY.finditer(candidate), literal_entity)
    extra = unmatched(ENTITY.finditer(candidate), ENTITY.finditer(source), literal_entity)
    missing_urls = [match for match in missing if match.group().startswith("http")]
    extra_urls = [match for match in extra if match.group().startswith("http")]
    missing_terms = [match for match in missing if not match.group().startswith("http")]
    findings = []
    url_pairs = list(zip(missing_urls, extra_urls, strict=False))
    if len(missing_urls) != len(extra_urls):
        url_pairs = []
        free = list(extra_urls)
        for before in missing_urls:
            scored = [(SequenceMatcher(None, literal_entity(before), literal_entity(after)).ratio(), k) for k, after in enumerate(free)]
            if scored and (best := max(scored))[0] >= 0.5:
                url_pairs.append((before, free.pop(best[1])))
    paired = {id(before) for before, _ in url_pairs}
    for before, after in url_pairs:
        start, end = entity_span(after)
        findings.append(
            Finding(
                "entity",
                "major",
                Span("translation", start, end),
                "entities",
                candidate[start:end],
                f"URL changed from {literal_entity(before)!r}.",
                0.98,
                Span(field, *entity_span(before)),
            )
        )
    for match in missing_urls:
        if id(match) not in paired:
            start, end = entity_span(match)
            findings.append(
                Finding(
                    "entity",
                    "major",
                    Span(field, start, end),
                    "entities",
                    source[start:end],
                    "Literal identifier missing. Non-URL identifiers may be valid transliterations.",
                    0.98,
                )
            )
    for match in missing_terms:
        findings.append(
            Finding(
                "entity",
                "minor",
                Span(field, match.start(), match.end()),
                "entities",
                match.group(),
                "Literal identifier missing. Non-URL identifiers may be valid transliterations.",
                0.58,
            )
        )
    return findings


def literal_entity(match: re.Match[str]) -> str:
    """Preserve case-sensitive URL paths and ignore sentence-final punctuation."""
    value = match.group()
    return value.rstrip(".,;!") if value.startswith("http") else value.casefold()


def char_script(char: str) -> str:
    """Unicode script family of one letter, from its character name."""
    return unicodedata.name(char, "").split(" ")[0]


def script_mask(text: str) -> str:
    """Hide content that legitimately stays in Latin: code, math, URLs, link targets, tags, emails."""
    return mask(text, (FENCE, INLINE_CODE, FORMULA, LINK_TARGET, URL, HTML_OR_EMAIL))


def wrong_script_segments(candidate: str, masked: str, expected: str) -> list[Finding]:
    """Flag sentences mostly written in another known non-Latin script than the target's."""
    flagged: list[tuple[int, int, str]] = []
    previous = False
    for segment in SENTENCE.finditer(masked):
        scripts = Counter(char_script(char) for char in segment.group() if char.isalpha())
        letters = sum(scripts.values())
        if not letters:
            continue
        script, count = scripts.most_common(1)[0]
        if script != expected and script in KNOWN_SCRIPTS and script != "LATIN" and count >= 8 and count >= 0.6 * letters:
            gap = masked[flagged[-1][1] : segment.start()] if flagged else "\n"
            if previous and flagged[-1][2] == script and "\n" not in gap:
                flagged[-1] = (flagged[-1][0], segment.end(), script)
            else:
                flagged.append((segment.start(), segment.end(), script))
            previous = True
        else:
            previous = False
    findings = []
    for start, end, script in flagged:
        span = trimmed(candidate, start, end, "translation")
        findings.append(
            Finding(
                "wrong_language",
                "major",
                span,
                "script",
                candidate[span.start : span.end],
                f"This text is in {script} script; the target language uses {expected}.",
                0.9,
            )
        )
    return findings


def word_grams(words: list[str], size: int) -> set[tuple[str, ...]]:
    """All contiguous word n-grams of one size."""
    return {tuple(words[i : i + size]) for i in range(len(words) - size + 1)}


def latin_runs(masked: str) -> list[list[re.Match[str]]]:
    """Group consecutive Latin words separated only by spaces, digits, or punctuation on one line."""
    runs: list[list[re.Match[str]]] = []
    for word in LATIN_WORD.finditer(masked):
        if runs:
            gap = masked[runs[-1][-1].end() : word.start()]
            if all(unicodedata.category(char)[0] not in "LM" and char not in "\n|" for char in gap):
                runs[-1].append(word)
                continue
        runs.append([word])
    return runs


def is_name_like(word: str) -> bool:
    """Capitalized, all-caps, or camel-case words are names, acronyms, or brands."""
    return word[0].isupper() or any(char.isupper() for char in word[1:])


SEGMENT_BREAKS = "\n|.!?।॥۔"


def latin_dominates(masked: str, start: int, end: int) -> bool:
    """True when Latin-script words are the majority of the sentence, line, or cell around a span."""
    left = max(masked.rfind(char, max(0, start - 2000), start) for char in SEGMENT_BREAKS) + 1
    rights = [masked.find(char, end, end + 2000) for char in SEGMENT_BREAKS]
    right = min((index for index in rights if index >= 0), default=min(len(masked), end + 2000))
    tokens = [token for token in masked[left:right].split() if any(unicodedata.category(char)[0] == "L" for char in token)]
    latin = sum(any(char.isascii() and char.isalpha() for char in token) for token in tokens)
    return latin > len(tokens) / 2


def untranslated_runs(candidate: str, masked: str, source: str | None) -> list[Finding]:
    """Flag runs of English prose words inside a non-Latin-script translation."""
    source_words = [word.casefold() for word in LATIN_WORD.findall(script_mask(source))] if source else []
    grams3, grams4, vocabulary = word_grams(source_words, 3), word_grams(source_words, 4), set(source_words)
    findings = []
    for run in latin_runs(masked):
        words = [match.group() for match in run]
        lowercase = [word for word in words if not is_name_like(word)]
        # Kept terms and names are short and Title Case ("Indian Space Research
        # Organisation", "Google Maps API"); prose has lowercase content words.
        if len(words) < 3 or len(lowercase) < 2 or not any(len(word) >= 4 for word in lowercase):
            continue
        start, end = run[0].start(), run[-1].end()
        before = masked[max(0, start - 16) : start].rstrip()[-1:]
        after = masked[end : end + 16].lstrip(" .,;:")[:1]
        if before == "(" and after == ")":
            continue  # a parenthetical English gloss after a translated term
        folded = [word.casefold() for word in words]
        if word_grams(folded, 4) & grams4:
            strength, detail = 0.9, "English source text left untranslated."
        elif word_grams(folded, 3) & grams3 and latin_dominates(masked, start, end):
            strength, detail = 0.8, "A short English source phrase left untranslated."
        elif word_grams(folded, 3) & grams3:
            # Three source words inside a translated sentence are usually a kept
            # command, quotation, or term ("pip install numpy"), so stay soft.
            strength, detail = 0.66, "A short English phrase kept inside a translated sentence."
        elif source_words and sum(word in vocabulary for word in folded) >= 0.5 * len(folded):
            strength, detail = 0.7, "English text that reuses source words; possibly untranslated."
        else:
            strength, detail = 0.66, "Latin-script prose in a non-Latin target. Check for valid romanization or code-mix."
        span = Span("translation", start, end)
        findings.append(Finding("wrong_language", "major", span, "script", candidate[start:end], detail, strength))
    return findings


def script_findings(candidate: str, lang: str, source: str | None = None) -> list[Finding]:
    """Flag output in the wrong script or left in English, per passage and per sentence."""
    expected = SCRIPTS.get(lang)
    if expected is None or not candidate.strip():
        return []
    masked = script_mask(candidate)
    letters = [(i, char_script(char)) for i, char in enumerate(masked) if char.isalpha()]
    if len(letters) >= 8 and sum(script == expected for _, script in letters) < len(letters) * 0.08:
        span = Span("translation", letters[0][0], letters[-1][0] + 1)
        evidence = candidate[span.start : span.end]
        dominant = Counter(script for _, script in letters).most_common(1)[0][0]
        if lang != "en" and source is not None and " ".join(candidate.split()) == " ".join(source.split()):
            detail, strength = "The candidate reproduces the English source.", 0.97
        elif dominant != "LATIN" and dominant in KNOWN_SCRIPTS:
            detail, strength = f"The candidate is written in {dominant} script; the target language uses {expected}.", 0.95
        elif dominant == "LATIN" and source and copied_share(masked, source) >= 0.5:
            detail, strength = "The candidate is largely the untranslated English source.", 0.93
        else:
            detail, strength = f"Very few letters use the expected {expected} script. Check for valid romanization or code-mix.", 0.68
        return [Finding("wrong_language", "major", span, "script", evidence, detail, strength)]
    findings = wrong_script_segments(candidate, masked, expected)
    if expected != "LATIN":
        findings.extend(untranslated_runs(candidate, masked, source))
    return findings


def copied_share(masked_candidate: str, source: str) -> float:
    """Share of the candidate's Latin word trigrams that occur in the source."""
    words = [word.casefold() for word in LATIN_WORD.findall(masked_candidate)]
    source_words = [word.casefold() for word in LATIN_WORD.findall(script_mask(source))]
    size = 3 if len(words) >= 3 else len(words)
    if not size:
        return 0.0
    grams = [tuple(words[i : i + size]) for i in range(len(words) - size + 1)]
    known = word_grams(source_words, size)
    return sum(gram in known for gram in grams) / len(grams)
