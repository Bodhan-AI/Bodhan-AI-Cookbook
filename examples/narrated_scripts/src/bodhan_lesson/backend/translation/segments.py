"""Plan translation slots, masking exact source literals before budgeting requests."""

import json
import re
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import yaml

from ..models import Glossary, SegmentAddress, SourceBlock, SourceChapter, TranslationPlan, TranslationUnit
from ..ocr.extraction import MATH_SPAN

CONTEXT_LIMIT = 28 * 1024
REQUEST_RESERVE = 1024
OUTPUT_GROWTH_FACTOR = 2
TOKEN = re.compile(r"__BODHAN_[0-9a-f]{24}_\d+_\d+__")

NUMBER = r"[+\-−±]?\d+(?:[,.٫٬]\d+)*(?:/\d+)?(?:[eE][+\-−]?\d+)?"
UNITS = (
    r"(?:km|cm|mm|nm|[µμ]m|m|kg|mg|[µμ]g|g|mL|ml|L|l|ms|min|hrs?|h|s|"
    r"kHz|MHz|Hz|kPa|Pa|kW|kWh|W|kJ|J|mV|V|mA|A|mol|N|K|"
    r"किमी|सेमी|मिमी|मीटर|किग्रा|ग्राम)"
    r"(?:[²³]|\^?[23])?"
)
UNIT_NAMES = (
    r"(?i:degrees?|percent|(?:milli|centi|kilo)?met(?:ers?|res?)|"
    r"(?:milli|kilo)?grams?|(?:milli)?lit(?:ers?|res?)|seconds?|minutes?|hours?|days?|डिग्री)"
)
# Short uppercase labels can identify geometry points. Common English function
# words still need translation when a textbook sets its headings in capitals.
UPPERCASE_WORDS = frozenset({
    "AM", "AN", "AND", "ARE", "AS", "AT", "BE", "BUT", "BY", "CAN", "DO", "FOR", "HAD",
    "HAS", "HOW", "IF", "IN", "IS", "IT", "NOT", "OF", "ON", "OR", "OUR", "THE", "TO",
    "UP", "WAS", "WE", "WHO", "WHY", "YOU",
})
LITERAL_SPAN = re.compile(
    rf"""
    (?<!\w)(?:
        (?i:fig(?:ure)?\.?|diagram|table|eq(?:uation)?\.?|chapter|section|exercise|
            example|चित्र|सारणी|तालिका|अध्याय|खंड|प्रश्न)
        [ \t]*\d+(?:[.-]\d+)*[a-zA-Z]?(?:\([a-zA-Z\d]+\))?
        | [∠△Δ][ \t]*[A-Z]{{1,3}}\d*
        | [A-Z]{{2,3}}
        | [A-Za-z][A-Za-z0-9_-]*?\d[A-Za-z0-9_-]*
        | {NUMBER}(?:
            [ \t]*(?:%|‰|°(?:[ \t]*[CF])?|{UNITS}(?:[·/]{UNITS})*)
            | [ \t]+{UNIT_NAMES}
        )?
    )(?!\w)
    """,
    re.VERBOSE,
)


def load_glossary(path: Path) -> Glossary:
    """Read source terms mapped to one shared term or to terms by target code."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeError) as error:
        raise ValueError(f"Invalid glossary YAML in {path}.") from error
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("The glossary must map source terms to target terms.")

    glossary: Glossary = {}
    for source, targets in data.items():
        if not isinstance(source, str) or not source.strip():
            raise ValueError("Glossary source terms must be nonempty strings.")
        if isinstance(targets, str) and targets.strip():
            glossary[source] = targets
        elif isinstance(targets, dict) and targets and all(
            isinstance(code, str) and code.strip() and isinstance(term, str) and term.strip()
            for code, term in targets.items()
        ):
            glossary[source] = targets
        else:
            raise ValueError(f"Glossary term {source!r} needs a target string or a language-to-term map.")
    return glossary


def literal_matches(text: str) -> Iterator[re.Match[str]]:
    end = 0
    for math in MATH_SPAN.finditer(text):
        yield from (match for match in LITERAL_SPAN.finditer(text, end, math.start())
                    if match[0] not in UPPERCASE_WORDS)
        yield math
        end = math.end()
    yield from (match for match in LITERAL_SPAN.finditer(text, end) if match[0] not in UPPERCASE_WORDS)


def protect_spans(text: str, unit_id: str) -> tuple[str, dict[str, str]]:
    """Replace each occurrence independently and avoid namespaces already in the source."""
    return mask_spans(text, unit_id, literal_matches(text))


def mask_spans(
    text: str, unit_id: str, matches: Iterator[re.Match[str]],
) -> tuple[str, dict[str, str]]:
    """Mask ordered, non-overlapping spans and retain their exact source spelling."""
    digest = sha256(unit_id.encode("utf-8")).hexdigest()[:24]
    salt = 0
    prefix = f"__BODHAN_{digest}_{salt}_"
    while prefix in text:
        salt += 1
        prefix = f"__BODHAN_{digest}_{salt}_"

    spans = {}
    parts = []
    end = 0
    for index, match in enumerate(matches, 1):
        token = f"{prefix}{index}__"
        spans[token] = match[0]
        parts.extend((text[end:match.start()], token))
        end = match.end()
    parts.append(text[end:])
    return "".join(parts), spans


def restore_spans(payload: str, protected_spans: Mapping[str, str]) -> str:
    """Restore each expected placeholder exactly once or identify the invalid token."""
    counts = Counter(match[0] for match in TOKEN.finditer(payload))
    for token in protected_spans:
        if counts[token] != 1:
            raise ValueError(f"Expected placeholder {token} once, found {counts[token]}.")
    return TOKEN.sub(lambda match: protected_spans.get(match[0], match[0]), payload)


def block_slots(block: SourceBlock) -> Iterator[tuple[str, SegmentAddress, str]]:
    if block.kind == "table":
        for row_index, row in enumerate(block.rows or (), 1):
            for column, cell in enumerate(row, 1):
                yield (block.cell_unit_id(row_index, column),
                       SegmentAddress(block.id, "table_cell", row=row_index, column=column),
                       cell.text)
    elif block.kind == "list":
        for index, item in enumerate(block.items or (), 1):
            yield block.item_unit_id(index), SegmentAddress(block.id, "list_item", item=index), item.text
    elif block.kind == "equation":
        if block.latex is not None:
            yield block.id, SegmentAddress(block.id, block.kind), block.latex
    elif block.kind != "figure" and block.text:
        yield block.id, SegmentAddress(block.id, block.kind), block.text


def _size(text: str) -> int:
    # UTF-8 bytes are a conservative proxy for tokens, including Indic scripts.
    return len(text.encode("utf-8"))


def _split_payload(payload: str, budget: int) -> list[tuple[str, str]]:
    """Return (part, following whitespace), or no parts when a word cannot fit."""
    parts = []
    start = end = size = 0
    for word in re.finditer(r"\S+", payload):
        gap = payload[end:word.start()]
        word_size = _size(word[0])
        next_size = size + _size(gap) + word_size
        if next_size > budget:
            if start == end or word_size > budget:
                return []
            parts.append((payload[start:end], gap))
            start, next_size = word.start(), word_size
        end, size = word.end(), next_size

    if end == 0:
        return []
    tail = payload[end:]
    if size + _size(tail) <= budget:
        parts.append((payload[start:], ""))
    else:
        parts.append((payload[start:end], tail))
    return parts


def _fit_unit(unit: TranslationUnit, budget: int) -> Iterator[TranslationUnit]:
    if _size(unit.payload) <= budget:
        yield unit
        return

    if not unit.literal and unit.address.kind in {"paragraph", "caption", "list_item", "footnote", "sidebar"}:
        parts = _split_payload(unit.payload, budget)
        if parts:
            for index, (payload, join_after) in enumerate(parts, 1):
                spans = {token: unit.protected_spans[token] for token in TOKEN.findall(payload)
                         if token in unit.protected_spans}
                yield replace(unit, id=f"{unit.id}:p{index:02d}", parent_id=unit.id, part=index,
                              payload=payload, protected_spans=spans, join_after=join_after)
            return

    yield replace(unit, status="failed", reason=f"Unit cannot fit the {budget}-byte payload budget.")


def plan_units(
    chapter: SourceChapter, glossary: Glossary | None = None, *, context_limit: int = CONTEXT_LIMIT,
) -> TranslationPlan:
    """Plan in canonical reading order, leaving room for glossary and output growth."""
    terms = glossary if glossary is not None else {}
    glossary_size = _size(json.dumps(terms, ensure_ascii=False))
    budget = max(0, (context_limit - REQUEST_RESERVE - glossary_size) // (1 + OUTPUT_GROWTH_FACTOR))
    units = []
    for section in chapter.sections:
        for block in section.blocks:
            if not block.eligible:
                continue
            for unit_id, address, text in block_slots(block):
                literal = address.kind in {"equation", "code"} or not text.strip()
                payload, spans = (text, {}) if literal else protect_spans(text, unit_id)
                unit = TranslationUnit(unit_id, address, section.id, block.id, block.source_block_ids,
                                       payload, spans, literal)
                units.extend(_fit_unit(unit, budget))
    return TranslationPlan(chapter.source_lang, terms, context_limit, budget, tuple(units))
