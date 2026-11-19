"""Translation engine: chunking within Indic-Translate's limits, table preservation,
truncation recovery and verification. Used by translate_text, create_subtitles and
verify_translation."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from .. import errors as E
from ..languages import (
    KNOWN_UNSUPPORTED,
    LANG_TO_SCRIPT,
    LANGUAGE_NAMES,
    SCRIPT_TO_LANGS,
    TRANSLATE_LANGUAGES,
    detect_language,
    indic_fraction,
    language_label,
    normalize_language,
    script_matches_language,
    supported_list,
)
from ..runtime import RunContext, settle
from ..textutil import (
    chrf,
    extract_numbers,
    letters_only,
    number_check,
    pack_segments,
    parse_table,
    render_table,
    repetition_score,
    split_markdown_blocks,
    table_shapes,
)

PARA = "\n\n"  # paragraph separator in Markdown
MAX_PARAGRAPH_UNITS = 80
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
MAX_PIECE_CHARS = 3000  # hard per-request input bound (well inside the 32K-token context)
CHUNK_CHARS = 1500  # well inside the 32K-token context; smaller chunks translate more faithfully
MAX_SPLIT_DEPTH = 3


def check_target(lang: str, *, script: str = "native") -> str:
    code = normalize_language(lang)
    if code in TRANSLATE_LANGUAGES:
        if script == "roman" and code == "en":
            raise E.ToolError(E.INVALID_ARGUMENT, "script='roman' cannot be used with English.", hint="Use script='native'.")
        if script == "codemix" and code in ("en", "ks-Deva", "sd-Arab"):
            raise E.ToolError(E.INVALID_ARGUMENT, f"script='codemix' is not available for {code}.", hint="Use script='native'.")
        return code
    name = KNOWN_UNSUPPORTED.get(code) or KNOWN_UNSUPPORTED.get(lang.strip().lower()) or lang
    raise E.ToolError(
        E.UNSUPPORTED_LANGUAGE,
        f"Indic-Translate does not support {name!s}. It translates between English and Indian languages only.",
        hint=f"Supported target codes: {supported_list(TRANSLATE_LANGUAGES)}. Do not substitute another language or model; report the task as unsupported.",
        details={"requested": lang, "supported": sorted(TRANSLATE_LANGUAGES)},
    )



def check_source(lang: str | None) -> str | None:
    """An explicitly named source language must be one Indic-Translate supports; 'auto' is fine.
    Checked before anything is sent, so an unsupported request costs nothing."""
    if not lang or lang == "auto":
        return None
    code = normalize_language(lang)
    if code in TRANSLATE_LANGUAGES:
        return code
    name = KNOWN_UNSUPPORTED.get(code) or KNOWN_UNSUPPORTED.get(lang.strip().lower()) or lang
    raise E.ToolError(E.UNSUPPORTED_LANGUAGE, f"Indic-Translate does not support {name} as a source language.",
                      hint=f"Supported source codes: {supported_list(TRANSLATE_LANGUAGES)}, or 'auto'. Report the task as unsupported.",
                      details={"requested_source": lang})


@dataclass
class Unit:
    kind: str  # "text" | "table" | "keep"
    source: str
    output: str = ""
    back: str | None = None
    calls: int = 0
    notes: list[str] = field(default_factory=list)
    attach_next: bool = False  # joined to the next unit by a single newline (a page marker split off its text)


def _already_in(text: str, target: str, source_hint: str | None = None) -> bool:
    """True when a paragraph is already written in the target language (e.g. an English
    line inside a Hindi notice being translated to English). Such text is kept verbatim:
    translating it wastes money and the model tends to drop it.

    A script used by one language only (Latin for English here, Tamil, Telugu, ...) is
    proof by itself. For scripts several languages share (Devanagari, Bengali,
    Perso-Arabic) the script proves nothing, so such text is kept only when the caller
    said the source is the target language."""
    if not letters_only(text):
        return True  # numbers/punctuation only
    if target == "en":
        # Latin script alone is not English (romanized Hindi, French): require English evidence.
        # Any Indian-language letter means part of the passage still needs translating.
        return indic_fraction(text) == 0 and script_matches_language(text, "en") > 0.9 and detect_language(text).language == "en"
    script = LANG_TO_SCRIPT.get(target)
    if script is None or script_matches_language(text, target) < 0.9:
        return False
    if len(SCRIPT_TO_LANGS.get(script, [])) > 1:
        return source_hint == target
    return True


def count_sentences(text: str) -> int:
    """Sentences by terminator only; OCR line wraps inside a sentence do not count."""
    flat = " ".join(text.split())
    return len([x for x in re.split(r"(?<=[.?!।॥۔᱾])\s+", flat) if letters_only(x)])


def plan_units(source: str, target: str | None = None, script: str = "native", source_hint: str | None = None) -> list[tuple[Unit, str]]:
    """Return (unit, separator-before) pairs that re-join to the document layout."""
    units: list[tuple[Unit, str]] = []
    buf: list[str] = []

    def flush() -> None:
        if not buf:
            return
        text = PARA.join(buf)
        buf.clear()
        if len(text) <= CHUNK_CHARS:
            units.append((Unit("text", text), PARA))
        else:
            for i, seg in enumerate(pack_segments(text, CHUNK_CHARS)):
                units.append((Unit("text", seg), PARA if i == 0 else " "))

    blocks = [b for b in split_markdown_blocks(source) if b.kind != "blank"]
    # Indic-Translate merges paragraphs it receives together, so by default each paragraph
    # is its own request (layout preserved, no re-translation). Very long documents group
    # paragraphs instead, to stay well under the 120 requests/minute limit; the paragraph
    # coverage check in translate_markdown repairs any merge.
    group = sum(1 for b in blocks if b.kind == "text") > MAX_PARAGRAPH_UNITS
    split: list = []
    glued: set[int] = set()
    for block in blocks:  # a page marker on its own line is never sent to the model
        lines = block.text.split(chr(10)) if block.kind == "text" else []
        if len(lines) > 1 and _HTML_COMMENT.fullmatch(lines[0].strip()):
            glued.add(len(split))
            split.append(type(block)("text", lines[0]))
            split.append(type(block)("text", chr(10).join(lines[1:])))
        else:
            split.append(block)
    blocks = split
    for i, block in enumerate(blocks):
        if block.kind == "text" and _HTML_COMMENT.fullmatch(block.text.strip()):
            flush()
            units.append((Unit("keep", block.text, attach_next=i in glued), PARA))  # e.g. <!-- page 3 --> markers
        elif block.kind == "text" and target and script == "native" and _already_in(block.text, target, source_hint):
            flush()
            unit = Unit("keep", block.text)
            unit.notes.append("a passage already in the target language was kept as is")
            units.append((unit, PARA))
        elif block.kind == "text":
            if not group or sum(len(b) for b in buf) + len(block.text) > CHUNK_CHARS:
                flush()
            buf.append(block.text)
        else:
            flush()
            units.append((Unit("table" if block.kind == "table" else "keep", block.text), PARA))
    flush()
    if units:
        units[0] = (units[0][0], "")
    return units


async def _translate_piece(ctx: RunContext, text: str, target: str, script: str, depth: int = 0, purpose: str = "translate") -> tuple[str, int]:
    """Translate one piece; on truncation split it and translate the halves. Never
    returns silently partial text."""
    if len(text) > MAX_PIECE_CHARS and depth < MAX_SPLIT_DEPTH:
        # Never send more than the model's context comfortably holds: split before sending.
        pieces = pack_segments(text, MAX_PIECE_CHARS)
        if len(pieces) > 1:
            results = await settle(*[_translate_piece(ctx, x, target, script, depth + 1, purpose) for x in pieces])
            return " ".join(r[0] for r in results), sum(r[1] for r in results)
    meta, _ = await ctx.client.translate(text, target, script=script, purpose=purpose)
    if not meta["truncated"]:
        out = meta["translation"].strip()
        if not out and letters_only(text):
            raise E.ToolError(E.EMPTY_RESULT, "Indic-Translate returned an empty translation.", retryable=True)
        return out, 1
    pieces = pack_segments(text, max(80, len(text) // 2))
    if depth >= MAX_SPLIT_DEPTH or len(pieces) < 2:
        raise E.ToolError(E.TRUNCATED_OUTPUT, "Indic-Translate kept truncating this passage even after splitting it.",
                          hint="Report this passage as untranslated; do not present a partial translation as complete.",
                          details={"passage_start": text[:120]})
    ctx.emit({"type": "recovery", "action": "split_truncated_chunk", "depth": depth + 1, "pieces": len(pieces)})
    results = await settle(*[_translate_piece(ctx, p, target, script, depth + 1, purpose) for p in pieces])
    return " ".join(r[0] for r in results), sum(r[1] for r in results) + 1


async def _translate_table(ctx: RunContext, unit: Unit, target: str, script: str) -> None:
    if len(unit.source) > CHUNK_CHARS:  # too big to send whole: translate cell by cell from the start
        out, calls = "", 0
    else:
        out, calls = await _translate_piece(ctx, unit.source, target, script)
    unit.calls += calls
    if table_shapes(out) == table_shapes(unit.source):
        unit.output = out
        return
    # The model reflowed the table: translate cell by cell and rebuild it in code.
    unit.notes.append("table re-assembled cell by cell because whole-table translation changed its shape")
    ctx.emit({"type": "recovery", "action": "table_cellwise", "shape": table_shapes(unit.source)})
    rows = parse_table(unit.source)
    cells = sorted({c for r in rows for c in r if letters_only(c)})
    translated = await settle(*[_translate_piece(ctx, c, target, script, purpose="translate-cell") for c in cells])
    mapping = {c: t for c, (t, _) in zip(cells, translated)}
    unit.calls += sum(n for _, n in translated)
    unit.output = render_table([[mapping.get(c, c) for c in r] for r in rows])


def _table_rows(md: str) -> list[list[list[str]]]:
    return [parse_table(b.text) for b in split_markdown_blocks(md) if b.kind == "table"]


async def translate_markdown(ctx: RunContext, source: str, target: str, script: str = "native",
                             source_hint: str | None = None) -> tuple[str, list[Unit]]:
    planned = plan_units(source, target, script, source_hint)

    async def run(unit: Unit) -> None:
        if unit.kind == "keep":
            unit.output = unit.source
        elif unit.kind == "table":
            await _translate_table(ctx, unit, target, script)
        else:
            unit.output, unit.calls = await _translate_piece(ctx, unit.source, target, script)
            paras = [b for b in unit.source.split(PARA) if b.strip()]
            got = [b for b in unit.output.split(PARA) if b.strip()]
            if len(paras) > 1 and len(got) < len(paras):
                # Coverage check: the model merged or dropped paragraphs. Re-translate them one by one
                # (each is a new input, so nothing is sent twice) and keep the layout.
                ctx.emit({"type": "recovery", "action": "paragraph_coverage_retranslate", "expected": len(paras), "got": len(got)})
                outs = await settle(*[_translate_piece(ctx, b, target, script) for b in paras])
                unit.output = PARA.join(o for o, _ in outs)
                unit.calls += sum(n for _, n in outs)
                unit.notes.append(f"a passage came back with {len(got)} of {len(paras)} paragraphs, so it was re-translated paragraph by paragraph")

    await settle(*[run(u) for u, _ in planned])
    text, prev = "", None
    for u, sep in planned:
        text += (chr(10) if prev is not None and prev.attach_next else sep) + u.output
        prev = u
    return text, [u for u, _ in planned]


async def verify_units(ctx: RunContext, units: list[Unit], source_text: str, output_text: str, target: str, script: str,
                       source_lang: str | None, *, back_translate: bool = True) -> dict[str, Any]:
    """Code checks + back-translation. Returns a verification record with status
    pass / warn / fail and the reasons."""
    checks: dict[str, Any] = {}
    problems: list[str] = []
    warnings: list[str] = []

    if script == "native":
        share = script_matches_language(output_text, target)
        checks["target_script_share"] = round(share, 3)
        if share < 0.5:
            problems.append(f"only {share:.0%} of letters are in the {LANGUAGE_NAMES.get(target, target)} script")
        elif share < 0.8:
            warnings.append(f"{1 - share:.0%} of letters are not in the target script (names/terms kept in source script?)")

    nums = number_check(_HTML_COMMENT.sub("", source_text), _HTML_COMMENT.sub("", output_text))  # page markers are not content
    checks["numbers"] = nums
    if nums["missing"]:
        warnings.append(f"numbers not found in the translation: {nums['missing'][:8]} (may be spelled out as words)")
    if nums["unexpected"]:
        warnings.append(f"numbers in the translation that are not in the source: {nums['unexpected'][:8]}")
    # Tables: numbers must stay in their row (a global count cannot see two values swapped between rows).
    for src_rows, out_rows in zip(_table_rows(source_text), _table_rows(output_text)):
        if len(src_rows) == len(out_rows):
            # cell by cell when the row keeps its shape (catches values swapped between columns), else per row
            moved = [i for i, (a, b) in enumerate(zip(src_rows, out_rows))
                     if ([extract_numbers(c) for c in a] != [extract_numbers(c) for c in b] if len(a) == len(b)
                         else sorted(extract_numbers(" ".join(a))) != sorted(extract_numbers(" ".join(b))))]
            if moved:
                problems.append(f"table rows {moved[:6]} have different numbers than the source rows (values moved, changed or lost)")

    src_shapes, out_shapes = table_shapes(source_text), table_shapes(output_text)
    checks["tables"] = {"source": src_shapes, "output": out_shapes}
    if src_shapes != out_shapes:
        problems.append(f"table structure changed: source {src_shapes} vs output {out_shapes}")

    short = [u.source[:60] for u in units if u.kind == "text" and count_sentences(u.source) >= 2
             and count_sentences(u.output) < count_sentences(u.source) - (1 if count_sentences(u.source) >= 4 else 0)]
    checks["sentence_coverage"] = {"passages_with_fewer_sentences": len(short)}
    if short:
        warnings.append(f"{len(short)} passage(s) came back with noticeably fewer sentences than the source (merged or omitted?): {short[:3]}")

    rep_src, rep_out = repetition_score(source_text), repetition_score(output_text)
    checks["repetition"] = {"source": round(rep_src, 3), "output": round(rep_out, 3)}
    if rep_out > rep_src + 0.25:
        warnings.append("translation repeats phrases much more than the source (possible looping)")

    def unit_lang(u: Unit) -> str | None:
        # Mixed documents: back-translate each passage into the language it was actually written in.
        guess = detect_language(u.source)
        if guess.script == "Latin" and guess.language is None:
            # A short Latin-script heading in an English document is English; inside an Indic document its
            # language is unknown, so there is no reference to back-translate into.
            return source_lang if LANG_TO_SCRIPT.get(source_lang or "") == "Latin" else None
        if guess.language in TRANSLATE_LANGUAGES and guess.confidence >= 0.6 and LANG_TO_SCRIPT.get(guess.language) != LANG_TO_SCRIPT.get(source_lang or ""):
            return guess.language
        return source_lang if source_lang in TRANSLATE_LANGUAGES else None

    todo: list[tuple[Unit, str]] = []
    if back_translate:
        for u in units:
            lang = unit_lang(u)
            if u.kind in ("text", "table") and letters_only(u.output) and lang and lang != target:
                todo.append((u, lang))
            elif u.kind in ("text", "table") and letters_only(u.output) and lang is None:
                warnings.append(f"a passage in an unidentified language was not back-translated: {u.source[:50]!r}")
        if source_lang is None and not todo:
            warnings.append("source language unknown; back-translation skipped")
    back_lang = todo[0][1] if todo else None
    if todo:
        backs = await asyncio.gather(*[_translate_piece(ctx, u.output, lang, "native", purpose="verify-back-translate") for u, lang in todo],
                                     return_exceptions=True)
        todo = [u for u, _ in todo]  # type: ignore[misc]
        scores, ratios, flagged = [], [], []
        for u, b in zip(todo, backs):
            if isinstance(b, BaseException):
                warnings.append(f"back-translation failed for one passage: {getattr(b, 'message', b)}")
                continue
            u.back = b[0]
            s = chrf(u.back, u.source)
            r = len(letters_only(u.back)) / max(len(letters_only(u.source)), 1)
            scores.append(s)
            ratios.append(r)
            if len(letters_only(u.source)) > 25 and (r < 0.6 or s < 0.15):
                # back-translation much shorter than the source: content was probably dropped
                flagged.append({"passage_start": u.source[:80], "back_translation_start": u.back[:80], "chrf": round(s, 3), "length_ratio": round(r, 2)})
        if scores:
            mean = sum(scores) / len(scores)
            checks["back_translation"] = {"language": back_lang, "mean_chrf": round(mean, 3), "min_chrf": round(min(scores), 3),
                                          "min_length_ratio": round(min(ratios), 2), "passages": len(scores)}
            if flagged:
                problems.append(f"{len(flagged)} passage(s) look incomplete or wrong after back-translation")
                checks["back_translation"]["flagged"] = flagged[:5]
            elif mean < 0.3:
                warnings.append(f"low back-translation agreement (chrF {mean:.2f}); meaning may have drifted")

    status = "fail" if problems else ("warn" if warnings else "pass")
    return {"status": status, "method": "script share, number and table preservation, repetition, back-translation (Indic-Translate) chrF",
            "problems": problems, "warnings": warnings, "checks": checks}


def detect_source(text: str, hint: str | None) -> tuple[str | None, dict]:
    if hint and hint != "auto":
        code = normalize_language(hint)
        return (code if code in LANGUAGE_NAMES else None), {"language": code, "source": "caller"}
    guess = detect_language(text)
    info = guess.as_dict()
    info["source"] = "script detection"
    return guess.language, info


__all__ = ["check_target", "translate_markdown", "verify_units", "detect_source", "Unit", "language_label"]
