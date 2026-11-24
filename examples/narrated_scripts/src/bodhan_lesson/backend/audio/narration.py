"""Plan ordered narration and build the exact text requests for synthesis."""

import json
import re
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import replace
from unicodedata import name

from ..config import DEFAULT_SPEECH_MAX_CHARS
from ..inference import ProviderError
from ..models import (
    ChapterEntry, NarrationEntry, NarrationPlan, NarrationScript, ScriptChunk, ScriptIssue, ScriptSection,
    SegmentAddress, SourceBlock, SourceChapter, SpokenNumber, TargetNarrationPlan, TranslationPlan, TranslationQARun,
    TranslationUnit, UnitTranslationQA,
)
from ..ocr.extraction import MATH_SPAN
from ..translation.provider import Translate
from ..translation.segments import block_slots, literal_matches, mask_spans, protect_spans
from ..translation.translate import translate_checked_english
from .spoken_numbers import spell_numbers

ProposeFigureNarration = Callable[[Mapping[str, str]], str]
FigureDraft = tuple[str, tuple[str, ...]]
CheckedFigureDraft = Callable[[str, str], FigureDraft | None]
CheckedSlots = Mapping[SegmentAddress, list[UnitTranslationQA]]

OPERATORS = {
    "+": "plus", "-": "minus", "−": "minus", "*": "times", "×": "times", r"\times": "times",
    "/": "divided by", "÷": "divided by", r"\div": "divided by", "=": "equals",
    "<": "is less than", ">": "is greater than",
}
ANGLE = r"\\angle\s*(?:\\mathrm\s*\{([A-Z]{1,4})\}|([A-Z]{1,4}))(?![A-Za-z])"
ATOM = rf"(?:{ANGLE}|\d+(?:[.,]\d+)?°?|[a-zA-Z])"
EQUATION_TOKEN = re.compile(rf"\s+|{ATOM}|\\(?:times|div)(?![a-zA-Z])|[()+\-−*×/÷=<>]")
SIMPLE_FRACTION = re.compile(r"\\frac\s*\{([^{}]+)\}\s*\{([^{}]+)\}")
DEGREE_SUFFIX = re.compile(r"(?<=\d)\s*\^\s*(?:\{\s*\\circ\s*\}|\\circ(?![A-Za-z]))")
DEGREES_NUMBER = re.compile(r"\d+(?:[.,]\d+)?(?= degrees\b)")
OVERLINE = re.compile(r"\\overline\s*\{\s*(?:\\(?:text|mathrm)\s*\{([A-Z]{1,4})\}|([A-Z]{1,4}))\s*\}")
ROMAN_PARTS = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6}
CAPTION_PART = re.compile(r"^\s*\((i|ii|iii|iv|v|vi)\)\s*", re.I)
ENGLISH_REFERENCE_LABEL = re.compile(r"(?<![A-Za-z])(?P<label>fig(?:ure)?\.?|chapter)(?=\s+\d)", re.I)
PARENTHETICAL = re.compile(r"\(([^()]*)\)")
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
FIGURE_REFERENCE = re.compile(r"(?:fig(?:ure)?\.?|diagram)\s*\d[\w.-]*\s*[:.]?", re.I)
SPEECH_BREAK = re.compile(
    r"(?P<sentence>[.!?।॥。！？؟۔\u1c7e\u1c7f]+|\r?\n(?:[ \t]*\r?\n)+)"
    r"|[,;:،؛，；：]+|(?<=\s)[—–](?=\s)|\r?\n"
)
SPEECH_BREAK_TAIL = re.compile(r"[”’\x22»]*(?P<space>[ \t]*)")
ABBREVIATION = re.compile(r"(?i)(?<!\w)(?:e\.g\.|i\.e\.|fig\.|eq\.|dr\.|mrs?\.|ms\.|prof\.|vs\.|(?:[a-z]\.){2,})")
EQUATION_VARIABLE = re.compile(r"(?<!\w)(?:[A-Z]{2,4}|[a-zA-Z])(?!\w)")
BRACKETS = {")": "(", "]": "[", "}": "{"}
NARRATION_POLICY_VERSION = 2
SCRIPT_POLICY_VERSION = 5
TAMIL_PREFERRED_CHARS = 180
TAMIL_PLAIN_PROSE_CHARS = 240
TAMIL_PROSE_MATH = re.compile(r"[\d$°]")


def _entry(section_id: str, block: ChapterEntry, policy: str) -> NarrationEntry:
    return NarrationEntry(section_id, block.id, block.source_block_ids or (block.id,), policy, "skip")


def _unit_ids(units: Sequence[UnitTranslationQA]) -> tuple[str, ...]:
    return tuple(unit.unit_id for unit in units)


def _checked_text(units: Sequence[UnitTranslationQA]) -> str | None:
    if not units or any(not unit.eligible or unit.text is None for unit in units):
        return None
    text = "".join((unit.text or "") + unit.join_after for unit in units)
    return text if text.strip() else None


def _unusable_reason(units: Sequence[UnitTranslationQA]) -> str:
    for unit in units:
        if not unit.eligible:
            issues = unit.retry_issues or unit.issues
            return issues[0].reason if issues else "The translation unit did not pass QA."
    return "The slot has no nonempty checked text."


def _reuse_units(
    entry: NarrationEntry, members: Sequence[UnitTranslationQA],
) -> Iterator[NarrationEntry]:
    text = _checked_text(members)
    if text is not None:
        yield replace(entry, unit_ids=_unit_ids(members), text=text, action="reuse_translation")
        return
    if not members:
        yield replace(entry, reason=_unusable_reason(members))
    for member in members:
        text = _checked_text((member,))
        if text is None:
            yield replace(entry, unit_ids=(member.unit_id,), reason=_unusable_reason((member,)))
        else:
            yield replace(entry, unit_ids=(member.unit_id,), text=text, action="reuse_translation")


def plan_table_narration(
    section_id: str, block: SourceBlock, checked: CheckedSlots,
) -> Iterator[NarrationEntry]:
    """Introduce evidenced headers and pair checked values with them, row by row."""
    rows = block.rows or ()
    entry = _entry(section_id, block, "table_row")
    if not any(rows):
        yield replace(entry, reason="The table has no readable cells.")
        return

    # A merged cell's physical column is ambiguous without a grid expansion.
    # Preserve reading order and avoid assigning a guessed label to a value.
    has_spans = any(cell.rowspan != 1 or cell.colspan != 1 for row in rows for cell in row)
    first_has_headers = any(cell.is_header is True for cell in rows[0])
    headers: dict[int, tuple[str, tuple[str, ...]]] = {}
    for row_index, row in enumerate(rows, 1):
        phrases = []
        used_ids = []
        skipped = []
        header_row = row_index == 1 and first_has_headers
        same_width = len(row) == len(rows[0])
        paired = False
        for column, cell in enumerate(row, 1):
            address = SegmentAddress(block.id, "table_cell", row=row_index, column=column)
            members = checked.get(address, [])
            unit_ids = _unit_ids(members)
            text = _checked_text(members)
            if text is None:
                skipped.append(replace(entry, unit_ids=unit_ids or (block.cell_unit_id(row_index, column),),
                                       reason=_unusable_reason(members)))
                continue
            if header_row and cell.is_header is True and not has_spans:
                headers[column] = (text.strip(), unit_ids)
            label = headers.get(column) if same_width and not header_row and cell.is_header is not True else None
            if label is not None:
                label_text, label_ids = label
                phrases.append(f"{label_text}: {text.strip()}")
                used_ids.extend(label_ids)
                paired = True
            else:
                phrases.append(text.strip())
            used_ids.extend(unit_ids)
        if phrases:
            evidence = "Read the checked cells in column order."
            if has_spans:
                evidence = "Merged cells require a literal row reading without inferred headers."
            elif header_row:
                evidence = "Introduce the first row's OCR-marked column headers."
            elif paired:
                evidence = "Pair values with checked first-row headers from the same column."
            yield replace(entry, policy="table_headers" if header_row else entry.policy,
                          action="reuse_translation", unit_ids=tuple(dict.fromkeys(used_ids)),
                          text=", ".join(phrases), evidence=evidence)
        yield from skipped


def _verbalize_equation(latex: str) -> str | None:
    """Read a bounded arithmetic grammar and mark both ends of every group."""
    # A fraction is one operand even when it is itself the divisor.
    expression = DEGREE_SUFFIX.sub("°", SIMPLE_FRACTION.sub(r"((\1)/(\2))", latex.strip().removesuffix(".")))
    words = []
    expects_atom = True
    depth = 0
    offset = 0
    for match in EQUATION_TOKEN.finditer(expression):
        if match.start() != offset:
            return None
        offset = match.end()
        token = match[0]
        if token.isspace():
            continue
        if expects_atom and token == "(":
            depth += 1
            words.append("open parenthesis")
        elif not expects_atom and token == ")" and depth:
            depth -= 1
            words.append("close parenthesis")
        elif expects_atom and re.fullmatch(ATOM, token):
            angle = re.fullmatch(ANGLE, token)
            if angle:
                words.append(f"angle {angle[1] or angle[2]}")
            else:
                words.append(token.removesuffix("°") + " degrees" if token.endswith("°") else token)
            expects_atom = False
        elif not expects_atom and token in OPERATORS and (token != "=" or not depth):
            words.append(OPERATORS[token])
            expects_atom = True
        else:
            return None
    if offset != len(expression) or depth or expects_atom:
        return None
    return " ".join(words) + "."


def _plan_equation(entry: NarrationEntry, members: Sequence[UnitTranslationQA]) -> NarrationEntry:
    entry = replace(entry, unit_ids=_unit_ids(members))
    latex = _checked_text(members)
    if latex is None:
        return replace(entry, reason=_unusable_reason(members))
    speech = _verbalize_equation(latex)
    if speech is None:
        return replace(entry, reason="The checked LaTeX uses unsupported notation.")
    return replace(entry, action="translate_english", text=speech,
                   evidence="Checked LaTeX read with explicit operator names and group boundaries.")


def validate_english_draft(raw: str, evidence: Mapping[str, str]) -> FigureDraft | None:
    """Accept a verbatim statement from a cited block so relations and literals stay intact."""
    try:
        draft = json.loads(raw)
    except (json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(draft, dict) or set(draft) != {"text", "block_ids"}:
        return None
    text, ids = draft["text"], draft["block_ids"]
    if (not isinstance(text, str) or not isinstance(ids, list) or len(ids) != 1
            or not isinstance(ids[0], str) or ids[0] not in evidence):
        return None
    text = " ".join(text.split())
    original = " ".join(evidence[ids[0]].split())
    if not text or FIGURE_REFERENCE.fullmatch(text) or text not in {original, *SENTENCE_BREAK.split(original)}:
        return None
    return text, (ids[0],)


def _plan_figure(
    entry: NarrationEntry, captions: tuple[ChapterEntry, ...], checked: CheckedSlots,
    english: Mapping[str, str], use_source_english: bool,
    propose: CheckedFigureDraft | None,
) -> Iterator[NarrationEntry]:
    if not captions:
        yield replace(entry, reason="The figure has no linked caption at this position.")
    for caption in captions:
        caption_ids = caption.source_block_ids or (caption.id,)
        source_ids = tuple(dict.fromkeys((*entry.source_block_ids, *caption_ids)))
        caption_entry = replace(entry, source_block_ids=source_ids, supporting_block_ids=(caption.id,))
        if not caption.eligible:
            yield replace(caption_entry, reason=caption.reason)
            continue
        members = checked.get(SegmentAddress(caption.id, "caption"), [])
        fallback = _checked_text(members)
        if fallback is None:
            yield from _reuse_units(caption_entry, members)
            continue
        caption_entry = replace(caption_entry, action="reuse_translation", unit_ids=_unit_ids(members),
                                text=fallback, evidence="Use the linked checked caption.")
        grounded = caption.text if use_source_english else ""
        if not use_source_english and all(unit.unit_id in english for unit in members):
            grounded = "".join(english[unit.unit_id] + unit.join_after for unit in members)
        if propose is not None and grounded.strip() and not FIGURE_REFERENCE.fullmatch(grounded.strip()):
            draft = propose(caption.id, grounded)
            if draft is not None:
                text, ids = draft
                yield replace(caption_entry, policy="figure_description", action="translate_english",
                              text=text, evidence="The cited caption states this relationship verbatim.",
                              supporting_block_ids=ids, fallback_text=fallback)
                continue
            caption_entry = replace(caption_entry, evidence="No proposed relationship passed grounding. "
                                    "Use the linked checked caption.")
        yield caption_entry


def _plan_section(
    section_id: str, blocks: tuple[ChapterEntry, ...], checked: CheckedSlots,
    english: Mapping[str, str], use_source_english: bool, propose: CheckedFigureDraft | None,
) -> Iterator[NarrationEntry]:
    index = 0
    while index < len(blocks):
        block = blocks[index]
        index += 1
        if not block.eligible:
            yield replace(_entry(section_id, block, "unavailable"), reason=block.reason)
        elif block.kind == "table":
            yield from plan_table_narration(section_id, block, checked)
        elif block.kind == "figure":
            end = index
            while end < len(blocks) and blocks[end].caption_for == block.id:
                end += 1
            yield from _plan_figure(_entry(section_id, block, "figure_caption"), blocks[index:end],
                                    checked, english, use_source_english, propose)
            index = end
        elif block.kind == "equation":
            yield _plan_equation(_entry(section_id, block, "equation_arithmetic"),
                                 checked.get(SegmentAddress(block.id, "equation"), []))
        elif block.kind in {"advertisement", "code", "footer", "header", "page_number"}:
            yield replace(_entry(section_id, block, block.kind),
                          reason="This block type is excluded from lesson speech.")
        else:
            entry = _entry(section_id, block, "list_item" if block.kind == "list" else block.kind)
            slots = tuple(block_slots(block))
            if not slots:
                yield replace(entry, reason="The block has no readable text slots.")
            for slot_id, address, _ in slots:
                if address not in checked:
                    yield replace(entry, unit_ids=(slot_id,), reason="The slot has no checked translation.")
                else:
                    yield from _reuse_units(entry, checked[address])


def plan_narration(
    chapter: SourceChapter, translation: TranslationQARun, *,
    propose_figure: ProposeFigureNarration | None = None,
) -> NarrationPlan:
    """Produce source-ordered decisions for each target without translating new speech."""
    english = {unit.unit_id: unit.text for unit in translation.english_pivots if unit.text is not None}
    english.update({unit.unit_id: unit.text for lesson in translation.lessons if lesson.qa.target_lang == "en"
                    for section in lesson.qa.sections for unit in section.units
                    if unit.eligible and unit.text is not None})
    drafts: dict[tuple[str, str], FigureDraft | None] = {}

    def checked_draft(block_id: str, caption: str) -> FigureDraft | None:
        assert propose_figure is not None
        key = (block_id, caption)
        if key not in drafts:
            evidence = {block_id: caption}
            try:
                drafts[key] = validate_english_draft(propose_figure(evidence), evidence)
            except ProviderError:
                drafts[key] = None
        return drafts[key]

    propose = checked_draft if propose_figure is not None else None
    targets = []
    for lesson in translation.lessons:
        sections = {section.section_id: section for section in lesson.qa.sections}
        entries = []
        for section in chapter.sections:
            checked: dict[SegmentAddress, list[UnitTranslationQA]] = defaultdict(list)
            qa = sections.get(section.id)
            if qa is not None:
                for unit in qa.units:
                    checked[unit.address].append(unit)
            decisions = tuple(_plan_section(section.id, section.blocks, checked, english,
                                            chapter.source_lang == "en", propose))
            entries.extend(decisions or (NarrationEntry(section.id, None, (), "empty_section", "skip",
                                                        reason="The source section has no blocks."),))
        targets.append(TargetNarrationPlan(lesson.qa.target_lang, lesson.qa.status, tuple(entries)))
    return NarrationPlan(chapter.source_lang, tuple(targets))


def _bracket_spans(text: str) -> Iterator[tuple[int, int]]:
    stack: list[tuple[str, int]] = []
    for index, character in enumerate(text):
        if character in "([{":
            stack.append((character, index))
        elif character in BRACKETS and stack:
            opener, start = stack.pop()
            if opener == BRACKETS[character]:
                yield start, index + 1
            else:
                stack.clear()


def _speech_break_offsets(text: str) -> tuple[list[int], list[int]]:
    protected = sorted((*_bracket_spans(text), *(match.span() for match in literal_matches(text)),
                        *(match.span() for match in ABBREVIATION.finditer(text))))
    sentences, clauses = [], []
    last_text = len(text.rstrip())
    span_index = protected_until = 0
    for match in SPEECH_BREAK.finditer(text):
        start, stop = match.span()
        while span_index < len(protected) and protected[span_index][0] <= start:
            protected_until = max(protected_until, protected[span_index][1])
            span_index += 1
        if start < protected_until:
            continue
        if (match[0] in {".", ","} and start > 0 and stop < len(text)
                and text[start - 1].isdecimal() and text[stop].isdecimal()):
            continue
        tail = SPEECH_BREAK_TAIL.match(text, stop)
        assert tail is not None
        offsets = sentences if match["sentence"] else clauses
        for offset in dict.fromkeys((tail.start("space"), tail.end())):
            if offset < last_text:
                offsets.append(offset)
    return sentences, clauses


def split_for_speech(text: str, max_chars: int = DEFAULT_SPEECH_MAX_CHARS) -> tuple[str, ...]:
    """Return lossless, bounded slices or raise when punctuation offers no safe split."""
    if max_chars < 1:
        raise ValueError("The speech character limit must be positive.")
    if not text.strip():
        return ()
    if len(text) <= max_chars:
        return (text,)

    sentences, clauses = _speech_break_offsets(text)
    chunks = []
    start = 0
    while len(text) - start > max_chars:
        for offsets in (sentences, clauses):
            index = bisect_right(offsets, start + max_chars) - 1
            if index >= 0 and offsets[index] > start and text[start:offsets[index]].strip():
                end = offsets[index]
                chunks.append(text[start:end])
                start = end
                break
        else:
            raise ValueError(f"The text cannot be split safely within {max_chars} characters.")
    return (*chunks, text[start:])


def split_speech_for_repair(chunk: ScriptChunk) -> tuple[ScriptChunk, ...]:
    """Split once at a safe punctuation boundary while keeping exact text and provenance."""
    if chunk.policy == "equation_arithmetic":
        return ()
    for offsets in _speech_break_offsets(chunk.text):
        viable = [offset for offset in offsets if chunk.text[:offset].strip() and chunk.text[offset:].strip()]
        if viable:
            cut = min(viable, key=lambda offset: abs(len(chunk.text) - 2 * offset))
            return tuple(
                replace(chunk, id=f"{chunk.id}-p{index:02d}", text=text)
                for index, text in enumerate((chunk.text[:cut], chunk.text[cut:]), 1)
            )
    return ()


def _translate_entry(
    entry: NarrationEntry, index: int, target: str, plan: TranslationPlan, translate: Translate, *, slot: str = "",
) -> tuple[str | None, str | None, tuple[str, ...]]:
    assert entry.text is not None and entry.block_id is not None
    unit_id = f"{entry.section_id}:n{index:02d}{slot}"
    is_equation = entry.policy == "equation_arithmetic"
    if is_equation:
        # Translate the spoken degree unit while protecting the value and label.
        literals = tuple(DEGREES_NUMBER.match(entry.text, match.start()) or match
                         for match in literal_matches(entry.text))
        variables = (match for match in EQUATION_VARIABLE.finditer(entry.text)
                     if not any(literal.start() <= match.start() < literal.end() for literal in literals))
        matches = sorted((*literals, *variables), key=lambda match: match.start())
        payload, protected = mask_spans(entry.text, unit_id, iter(matches))
    else:
        payload, protected = protect_spans(entry.text, unit_id)
    address = SegmentAddress(entry.block_id, "equation" if is_equation else "caption")
    unit = TranslationUnit(unit_id, address, entry.section_id, entry.block_id, entry.source_block_ids,
                           payload, protected)
    result = translate_checked_english(
        unit, target, plan, translate, preserve_literal_order=is_equation,
        spoken_degrees=is_equation and bool(DEGREES_NUMBER.search(entry.text)),
    )
    attempts = tuple(attempt_id for leg in result.legs for attempt_id in leg.attempt_ids)
    return result.text, result.reason, attempts


def _read_geometry(expression: str) -> str | None:
    expression = expression.strip()
    if expression == r"\angle":
        return "angle"
    overline = OVERLINE.fullmatch(expression)
    if overline:
        return f"{overline[1] or overline[2]} with an overline"
    if any(mark in expression for mark in (r"\circ", r"\angle", "°", "<", ">")):
        return _verbalize_equation(expression)
    return None


def _prepare_script_text(
    entry: NarrationEntry, text: str, index: int, target: str, plan: TranslationPlan, translate: Translate,
    geometry: dict[str, tuple[str | None, str | None, tuple[str, ...]]],
) -> tuple[str | None, str | None, tuple[str, ...]]:
    attempts = []

    def localize(english: str, slot: str) -> str | None:
        if english not in geometry:
            draft = replace(entry, policy="equation_arithmetic", action="translate_english", text=english)
            geometry[english] = _translate_entry(draft, index, target, plan, translate, slot=slot)
        spoken, _, ids = geometry[english]
        attempts.extend(ids)
        return spoken.strip().rstrip(".") if spoken else None

    text = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", text)
    if target == "ta":
        # Keep the exact reference number and localize the label. A comma
        # makes a checked parenthetical reference part of the spoken phrasing.
        text = PARENTHETICAL.sub(
            lambda match: f", {match[1]}" if ENGLISH_REFERENCE_LABEL.search(match[1]) else match[0], text,
        )
        reference_labels = {}
        for number, match in enumerate(ENGLISH_REFERENCE_LABEL.finditer(text), 1):
            label = "chapter" if match["label"].lower() == "chapter" else "figure"
            spoken = localize(label, f":ref{number:02d}")
            if not spoken:
                return None, f"Could not localize the reference label {label}.", tuple(attempts)
            reference_labels[match.start()] = spoken
        text = ENGLISH_REFERENCE_LABEL.sub(lambda match: reference_labels[match.start()], text)
    part = CAPTION_PART.match(text) if entry.policy in {"figure_caption", "figure_description"} else None
    if part:
        english = f"part {ROMAN_PARTS[part[1].lower()]}"
        spoken = localize(english, ":part")
        if not spoken:
            return None, f"Could not localize the caption label {english}.", tuple(attempts)
        text = f"{spoken}. {text[part.end():]}"

    fragments = []
    cursor = 0
    for number, match in enumerate(MATH_SPAN.finditer(text), 1):
        expression = next(group for group in match.groups() if group is not None).strip()
        if re.fullmatch(r"[A-Za-z]", expression):
            spoken = expression
        else:
            english = _read_geometry(expression)
            if english is None:
                continue
            spoken = localize(english, f":m{number:02d}")
            if not spoken:
                return None, f"Could not localize checked geometry: {expression}.", tuple(attempts)
        fragments.extend((text[cursor:match.start()], spoken))
        cursor = match.end()
    fragments.append(text[cursor:])
    return "".join(fragments), None, tuple(dict.fromkeys(attempts))


def _pack_tamil_prose(parts: tuple[str, ...], numbers: tuple[SpokenNumber, ...], max_chars: int) -> tuple[str, ...]:
    """Combine adjacent safe slices of one prose entry while retaining their original punctuation."""
    packed: list[str] = []
    for part in parts:
        if packed:
            combined = packed[-1] + part
            # Keep labels, mixed-script text and numeric readings on the shorter
            # path. A dense Tamil chunk in the sample exceeded 30 seconds at 180 characters.
            if (len(combined) <= max_chars and not TAMIL_PROSE_MATH.search(combined)
                    and not any(number.text in combined for number in numbers)
                    and not any(char.isalpha() and not name(char, "").startswith("TAMIL ") for char in combined)):
                packed[-1] = combined
                continue
        packed.append(part)
    return tuple(packed)


def _split_entry(
    entry: NarrationEntry, text: str, max_chars: int, language: str,
) -> tuple[tuple[str, tuple[SpokenNumber, ...]], ...]:
    text, numbers = spell_numbers(text, language)
    if entry.policy == "equation_arithmetic" and len(text) > max_chars:
        raise ValueError(f"The equation must stay together and exceeds {max_chars} characters.")
    parts = ()
    if language == "ta" and entry.policy != "equation_arithmetic" and max_chars > TAMIL_PREFERRED_CHARS:
        try:
            parts = split_for_speech(text, TAMIL_PREFERRED_CHARS)
        except ValueError:
            pass  # Preserve the text when the soft limit offers no safe boundary.
        if entry.policy == "paragraph" and len(parts) > 1:
            parts = _pack_tamil_prose(parts, numbers, min(max_chars, TAMIL_PLAIN_PROSE_CHARS))
    parts = parts or split_for_speech(text, max_chars)
    return tuple((part, tuple(number for number in numbers if number.text in part)) for part in parts)


def build_script(
    chapter: SourceChapter, narration: TargetNarrationPlan, plan: TranslationPlan, translate: Translate,
    *, voice: str | None = None, max_chars: int = DEFAULT_SPEECH_MAX_CHARS,
) -> NarrationScript:
    """Resolve each reading decision and retain every source section and its speech coverage."""
    if max_chars < 1:
        raise ValueError("The speech character limit must be positive.")
    entries: dict[str, list[NarrationEntry]] = defaultdict(list)
    for entry in narration.entries:
        entries[entry.section_id].append(entry)

    sections = []
    geometry = {}
    for section in chapter.sections:
        chunks = []
        issues = []
        for index, entry in enumerate(entries[section.id], 1):
            text, reason, attempts = entry.text, entry.reason, ()
            if entry.action == "skip":
                text = None
            elif entry.action == "translate_english" and text is not None:
                text, reason, attempts = _translate_entry(entry, index, narration.target_lang, plan, translate)

            if text is not None:
                text, preparation_reason, preparation_ids = _prepare_script_text(
                    entry, text, index, narration.target_lang, plan, translate, geometry,
                )
                reason = preparation_reason or reason
                attempts = tuple(dict.fromkeys((*attempts, *preparation_ids)))
            issue = ScriptIssue(entry.block_id, entry.source_block_ids, entry.unit_ids, entry.policy,
                                reason or "The entry has no usable speech text.", translation_attempt_ids=attempts)
            try:
                parts = _split_entry(entry, text, max_chars, narration.target_lang) if text is not None else ()
            except ValueError as error:
                parts, issue = (), replace(issue, reason=str(error))
            if not parts and entry.action == "translate_english" and entry.fallback_text is not None:
                try:
                    fallback, fallback_reason, fallback_ids = _prepare_script_text(
                        entry, entry.fallback_text, index, narration.target_lang, plan, translate, geometry,
                    )
                    attempts = tuple(dict.fromkeys((*attempts, *fallback_ids)))
                    issue = replace(issue, translation_attempt_ids=attempts)
                    if fallback is None:
                        raise ValueError(fallback_reason)
                    parts = _split_entry(entry, fallback, max_chars, narration.target_lang)
                except ValueError as error:
                    issue = replace(issue, reason=f"{issue.reason} Checked caption fallback failed. {error}")
                if parts:
                    issues.append(replace(issue, disposition="used_fallback"))
            if not parts:
                issues.append(issue)
                continue
            for part, numbers in parts:
                chunk_id = f"{section.id}-c{len(chunks) + 1:02d}"
                chunks.append(ScriptChunk(chunk_id, section.id, entry.source_block_ids, entry.unit_ids,
                                          part, entry.policy, attempts, spoken_numbers=numbers))
        reason = None
        if not chunks:
            reason = issues[0].reason if len(issues) == 1 else "No narration entry produced speech for this section."
        sections.append(ScriptSection(section.id, tuple(chunks), tuple(issues), reason))

    status = narration.translation_status
    if status == "completed" and any(section.issues or section.reason for section in sections):
        status = "completed_with_misses"
    return NarrationScript(narration.target_lang, voice, max_chars, status, tuple(sections))
