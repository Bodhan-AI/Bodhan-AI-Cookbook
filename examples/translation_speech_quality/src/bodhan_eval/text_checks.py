"""Deterministic translation checks and Unicode-safe evidence coordinates."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable

from markdown_it import MarkdownIt
from sacrebleu.metrics import CHRF

from .schema import Finding, Span

MARKDOWN = MarkdownIt("commonmark").enable("table")
CHRF_PLUS_PLUS = CHRF(word_order=2)
NUMBER = re.compile(
    r"(?<!\w)(?P<value>[+\-−]?\d+(?:[.,:/]\d+)*)(?:\s?(?P<unit>°[CF]|[%₹]|km|cm|mm|kg|mg|mL|Hz|kWh|m/s|s|m|g|L)(?!\w))?",
    re.IGNORECASE,
)
FORMULA = re.compile(r"(?<!\\)(\$\$[\s\S]+?\$\$|\$(?!\$)[^$\n]+?\$|\\\[[\s\S]+?\\\]|\\\([^\n]+?\\\))")
ENTITY = re.compile(r"https?://[^\s)>]+|(?<!\w)[A-Z][A-Z0-9]{1,}(?!\w)")
QUANTITY_LABEL = re.compile(r"(?im)^[ \t]*(?P<label>[\w][\w -]*?)[ \t]*:[ \t]*(?P<value>[^\n\r]+)")
UNIT_OBJECT = re.compile(r"\s+of\s+([\w-]+(?:[ \t]+[\w-]+)*?)(?=\s+(?:and|or|with|then|plus)\b|[.,;:\n|!?)]|$)", re.IGNORECASE)
TABLE_PIPE = re.compile(r"(?<!\\)\|")
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


def number_key(text: str) -> str:
    """Normalize script digits and unambiguous comma grouping, retaining units."""
    folded = unicodedata.normalize("NFKC", text).replace("−", "-")
    folded = "".join(str(unicodedata.decimal(char)) if char.isdecimal() else char for char in folded)
    return re.sub(r"(?<=\d),(?=\d{3}(?:,|\D|$))", "", "".join(folded.split())).casefold()


def unmatched(
    expected: Iterable[re.Match[str]],
    candidate: Iterable[re.Match[str]],
    key: Callable[[re.Match[str]], str],
) -> list[re.Match[str]]:
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


def numeric_value(match: re.Match[str]) -> str:
    """Compare the numeric value separately from the unit's written form."""
    return number_key(match["value"])


def context_key(text: str) -> str:
    """Normalize a literal label for matching without using word order or translation guesses."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def table_quantity_contexts(text: str):
    """Yield quantities in Markdown table cells with explicit row and column labels."""
    if "|" not in text:
        return
    columns = ()
    heading = False
    for token in MARKDOWN.parse(text):
        if token.type == "thead_open":
            heading = True
        elif token.type == "thead_close":
            heading = False
        elif token.type == "tr_open" and token.map:
            row = line_span(text, tuple(token.map), "source")
            raw = text[row.start : row.end].rstrip()
            cuts = [match.start() for match in TABLE_PIPE.finditer(raw)]
            boundaries = [-1, *cuts, len(raw)]
            cells = [(start + 1, end) for start, end in zip(boundaries, boundaries[1:], strict=False)]
            if cells and not raw[slice(*cells[0])].strip():
                cells = cells[1:]
            if cells and not raw[slice(*cells[-1])].strip():
                cells = cells[:-1]
            labels = tuple(context_key(raw[start:end]) for start, end in cells)
            if heading:
                columns = labels if len(set(labels)) == len(labels) and all(labels) else ()
            elif len(labels) > 1 and len(labels) == len(columns) and labels[0]:
                for column, (start, end) in zip(columns[1:], cells[1:], strict=True):
                    matches = list(NUMBER.finditer(text, row.start + start, row.start + end))
                    if len(matches) == 1:
                        yield ("table", labels[0], column), matches[0]


def contextual_quantities(text: str, numbers: list[re.Match[str]]) -> dict[tuple[str, ...], re.Match[str]]:
    """Index only unambiguous quantities attached to a shared literal label or table cell."""
    labels = defaultdict(list)
    for key, match in table_quantity_contexts(text):
        labels[key].append(match)
    for label in QUANTITY_LABEL.finditer(text):
        start, end = label.span("value")
        matches = list(NUMBER.finditer(text, start, end))
        if len(matches) == 1:
            labels[("field", context_key(label["label"]))].append(matches[0])
    for number in numbers:
        if number["unit"] and (label := UNIT_OBJECT.match(text, number.end())):
            labels[("of", context_key(label[1]))].append(number)
    return {key: matches[0] for key, matches in labels.items() if len(matches) == 1}


def number_findings(source: str, candidate: str, field: str) -> list[Finding]:
    """Localize changed values on either side and defer ambiguous spelled-out numbers."""
    actual = list(NUMBER.finditer(candidate))
    if not actual:
        return []
    original = list(NUMBER.finditer(source))
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
    # Keep the global check for unlabeled or translated contexts. Global
    # position alone cannot assign several changed values to source facts.
    original = [match for match in original if match.start() not in used_source]
    actual = [match for match in actual if match.start() not in used_actual]
    missing = unmatched(original, actual, numeric_value)
    extra = unmatched(actual, original, numeric_value)
    for before, after in zip(missing, extra, strict=False):
        findings.append(
            Finding(
                "number",
                "major",
                Span("translation", after.start(), after.end()),
                "numbers",
                after.group(),
                f"Unmatched candidate quantity. Comparison against {field} found {before.group()!r}.",
                0.88,
                Span(field, *before.span()) if len(missing) == len(extra) == 1 else None,
            )
        )
    for match in missing[len(extra) :]:
        findings.append(
            Finding(
                "number",
                "major",
                Span(field, match.start(), match.end()),
                "numbers",
                match.group(),
                "No matching numeric form in the candidate. Check if the value is spelled out.",
                0.58,
            )
        )
    for match in extra[len(missing) :]:
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


def quantity_key(match: re.Match[str]) -> str:
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
    for token in MARKDOWN.parse(text):
        if token.type in block_types:
            shape.append((token.type, token.tag, tuple(token.map) if token.map else None))
    return shape


def line_span(text: str, lines: tuple[int, int] | None, field: str) -> Span:
    """Convert a Markdown token's line range to an evidence span."""
    if lines is None:
        return Span(field, 0, len(text))
    rows = text.splitlines(keepends=True)
    start, end = lines
    return Span(field, sum(map(len, rows[:start])), sum(map(len, rows[:end])))


def formula_key(match: re.Match[str]) -> str:
    """Ignore whitespace when comparing the same LaTeX tokens."""
    return "".join(match.group().split())


def format_findings(source: str, candidate: str) -> list[Finding]:
    """Flag changed Markdown topology or a missing or altered LaTeX expression."""
    findings = []
    original, translated = markdown_shape(source), markdown_shape(candidate)
    if [entry[:2] for entry in original] != [entry[:2] for entry in translated]:
        # Table cells have no line ranges. Anchor to the nearest mapped row or block.
        mismatch = next(
            (i for i, (a, b) in enumerate(zip(original, translated, strict=False)) if a[:2] != b[:2]),
            min(len(original), len(translated)),
        )
        field, value, structure = ("translation", candidate, translated) if mismatch < len(translated) else ("source", source, original)
        lines = next((entry[2] for entry in reversed(structure[: mismatch + 1]) if entry[2]), None)
        span = line_span(value, lines, field)
        findings.append(
            Finding(
                "format",
                "major",
                span,
                "markdown",
                value[span.start : span.end],
                "Markdown headings, list items, code fences, or table shape changed.",
                0.98,
            )
        )
    for match in unmatched(FORMULA.finditer(source), FORMULA.finditer(candidate), formula_key):
        findings.append(
            Finding(
                "format",
                "major",
                Span("source", match.start(), match.end()),
                "latex",
                match.group(),
                "The original equation is missing or its LaTeX content was altered.",
                0.98,
            )
        )
    return findings


def entity_findings(source: str, candidate: str, field: str) -> list[Finding]:
    """Detect absent literal identifiers while keeping localizable acronyms as soft evidence."""
    return [
        Finding(
            "entity",
            "major" if match.group().startswith("http") else "minor",
            Span(field, match.start(), match.end()),
            "entities",
            match.group(),
            "Literal identifier missing. Non-URL identifiers may be valid transliterations.",
            0.98 if match.group().startswith("http") else 0.58,
        )
        for match in unmatched(ENTITY.finditer(source), ENTITY.finditer(candidate), literal_entity)
    ]


def literal_entity(match: re.Match[str]) -> str:
    """Preserve case-sensitive URL paths and ignore sentence-final punctuation."""
    value = match.group()
    return value.rstrip(".,;!") if value.startswith("http") else value.casefold()


def script_findings(candidate: str, lang: str, source: str | None = None) -> list[Finding]:
    """Treat a passage written almost entirely in another script as evidence, not language ID."""
    letters = [(i, unicodedata.name(char, "").split(" ")[0]) for i, char in enumerate(candidate) if char.isalpha()]
    expected = SCRIPTS[lang]
    if len(letters) < 8 or sum(script == expected for _, script in letters) >= len(letters) * 0.08:
        return []
    span = Span("translation", letters[0][0], letters[-1][0] + 1)
    copied = lang != "en" and source is not None and candidate.strip() == source.strip()
    return [
        Finding(
            "wrong_language",
            "major",
            span,
            "script",
            candidate[span.start : span.end],
            "The candidate reproduces the English source."
            if copied
            else f"Very few letters use the expected {expected} script. Check for valid romanization or code-mix.",
            0.97 if copied else 0.68,
        )
    ]
