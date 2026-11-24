"""Execute translation routes while keeping validation and provenance per unit."""

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import asdict, replace

from ..config import LANGUAGES
from ..models import (
    CheckedLesson, LanguageStatus, PlaceholderCheck, SectionTranslationQA, SegmentAddress, SourceBlock,
    SourceChapter, TargetTranslation, TargetTranslationQA, TermCheck, TranslatedUnit, TranslationIssue,
    TranslationLeg, TranslationPlan, TranslationQARun, TranslationRoute, TranslationRun, TranslationUnit,
    UnitTranslationQA,
)
from ..ocr.extraction import ATX_HEADING, SETEXT
from ..ocr.render import render_markdown
from .provider import Translate
from .segments import TOKEN, block_slots, literal_matches, mask_spans, restore_spans

SPOKEN_DEGREE_UNIT = re.compile(r"\s+(?:degrees?|डिग्री)$", re.IGNORECASE)


def route_translation(source: str, target: str) -> TranslationRoute:
    if source == target:
        return "reuse"
    if "en" in (source, target):
        return "direct"
    return "pivot"


def _term_matches(text: str, terms: Sequence[str]) -> Iterator[re.Match[str]]:
    if not terms:
        return
    alternatives = "|".join(re.escape(term) for term in sorted(terms, key=len, reverse=True))
    pattern = re.compile(rf"{TOKEN.pattern}|(?<!\w)(?P<term>{alternatives})(?!\w)", re.IGNORECASE)
    for match in pattern.finditer(text):
        if match["term"] is None:
            continue
        start, end = match.span()
        # Python's \w excludes combining marks, including Indic vowel signs.
        if start and unicodedata.category(text[start - 1]).startswith("M"):
            continue
        if end < len(text) and unicodedata.category(text[end]).startswith("M"):
            continue
        yield match


def _term_counts(text: str, terms: Sequence[str]) -> Counter[str]:
    return Counter(match[0].casefold() for match in _term_matches(text, terms))


def _translate_leg(
    unit_id: str, source: str, target: str, payload: str, budget: int, translate: Translate,
    *, repair: tuple[TranslationIssue, ...] = (),
) -> TranslationLeg:
    wire, alternate_tokens, aliases, wrapper = _repair_payload(payload, repair)
    if wrapper is not None and len(wire.encode("utf-8")) > budget:
        # A prose part can already occupy the full planned input budget.
        wire, wrapper = payload, None
    if len(wire.encode("utf-8")) > budget:
        return TranslationLeg(source, target, (), reason=f"Leg input exceeds the {budget}-byte payload budget.")
    response = translate(unit_id, source, target, wire)
    if response.text is None or not response.text.strip():
        return TranslationLeg(source, target, response.attempt_ids,
                              reason=response.reason or "Indic-Translate returned empty text.")

    expected = dict(Counter(TOKEN.findall(payload)))
    output = response.text
    unexpected = Counter()
    if alternate_tokens is not None:
        for token in alternate_tokens.findall(output):
            if token not in aliases:
                unexpected[token] += 1
        output = alternate_tokens.sub(lambda match: aliases.get(match[0], match[0]), output)
    if wrapper is not None:
        start, end = f"<{wrapper}>", f"</{wrapper}>"
        output = output.strip()
        if output.startswith(start) and output.endswith(end):
            output = output[len(start):-len(end)].strip()
        if start in output or end in output or not output:
            return TranslationLeg(source, target, response.attempt_ids, reason="Malformed retry wrapper.")
    observed = dict(Counter(TOKEN.findall(output)) + unexpected)
    if expected != observed:
        check = PlaceholderCheck(expected, observed, "failed")
        return TranslationLeg(source, target, response.attempt_ids, placeholder_check=check,
                              reason="Placeholder identities or occurrence counts changed.")
    check = PlaceholderCheck(expected, observed, "passed")
    return TranslationLeg(source, target, response.attempt_ids, output, check)


def _repair_payload(
    payload: str, issues: tuple[TranslationIssue, ...],
) -> tuple[str, re.Pattern[str] | None, dict[str, str], str | None]:
    if not issues:
        return payload, None, {}, None
    # Indic-Translate has one user text field. Use QA evidence to change how the
    # original content is presented without sending diagnostics as lesson text.
    tokens = tuple(dict.fromkeys(TOKEN.findall(payload)))
    if tokens and any(issue.check in {"placeholders", "literals", "translation"} for issue in issues):
        tag = "x"
        while f"<{tag} " in payload:
            tag += "x"
        names = {token: f'<{tag} id="{index}"/>' for index, token in enumerate(tokens, 1)}
        pattern = re.compile(rf'<{tag} id="\d+"/>')
        wire = TOKEN.sub(lambda match: names[match[0]], payload)
        return wire, pattern, {alias: token for token, alias in names.items()}, None

    tag = "segment"
    while f"<{tag}" in payload or f"</{tag}" in payload:
        tag += "x"
    return f"<{tag}>{payload}</{tag}>", None, {}, tag


def _expected_terms(unit: TranslationUnit, target: str, plan: TranslationPlan) -> tuple[TermCheck, ...]:
    selected = {}
    for term, targets in plan.glossary.items():
        expected = targets if isinstance(targets, str) else targets.get(target)
        if expected:
            selected[term.casefold()] = (term, expected)
    counts = _term_counts(unit.payload, tuple(term for term, _ in selected.values()))
    return tuple(TermCheck(term, expected, counts[key], 0, 0, "missing")
                 for key, (term, expected) in selected.items() if counts[key])


def _apply_terms(
    payload: str, unit_id: str, target: str, terms: tuple[TermCheck, ...],
    plan: TranslationPlan, through_pivot: bool,
) -> tuple[str, dict[str, str], Counter[str]]:
    # A pivot is shared across targets. Only apply a term on a target-specific leg
    # and use either its supplied English rendering or an exact match in the pivot.
    if not terms:
        return payload, {}, Counter()
    aliases: dict[str, list[TermCheck]] = {}
    spellings = {}
    for term in terms:
        source = term.source_term
        configured = plan.glossary[source]
        if through_pivot and isinstance(configured, dict):
            source = configured.get("en", source)
        key = source.casefold()
        spellings[key] = source
        aliases.setdefault(key, []).append(term)

    applied: Counter[str] = Counter()

    def eligible_matches() -> Iterator[re.Match[str]]:
        for match in _term_matches(payload, tuple(spellings.values())):
            candidates = aliases.get(match[0].casefold(), [])
            if len(candidates) != 1:
                continue
            [term] = candidates
            if applied[term.source_term] < term.expected_count:
                applied[term.source_term] += 1
                yield match

    masked, matched = mask_spans(payload, f"{unit_id}:glossary:{target}", eligible_matches())
    replacements = {token: aliases[alias.casefold()][0].expected_term for token, alias in matched.items()}
    return masked, replacements, applied


def translate_unit(
    unit: TranslationUnit, source: str | None, target: str, plan: TranslationPlan, translate: Translate,
    *, pivot: TranslatedUnit | None = None, use_glossary: bool = True,
    repair: tuple[TranslationIssue, ...] = (),
) -> TranslatedUnit:
    """Translate one unit and restore literals after the final leg passes its token check."""
    route = route_translation(source, target) if source in LANGUAGES and target in LANGUAGES else None
    if route is not None and route != "reuse" and unit.literal:
        route = "literal"
    result = TranslatedUnit(
        unit.id, unit.address, unit.section_id, unit.group_id, unit.source_block_ids, route,
        unit.protected_spans, unit.parent_id, unit.part, unit.join_after, None, None,
    )
    if route is None:
        reason = "Set --source-lang to translate an unresolved source language."
        if target not in LANGUAGES:
            reason = f"Unsupported target language: {target}."
        return replace(result, reason=reason)
    if unit.status == "failed":
        return replace(result, reason=unit.reason)
    if route in {"reuse", "literal"}:
        return replace(result, text=restore_spans(unit.payload, unit.protected_spans))

    assert source is not None
    terms = _expected_terms(unit, target, plan) if use_glossary else ()
    payload = unit.payload
    if route == "pivot":
        if pivot is None or pivot.unit_id != unit.id:
            raise ValueError(f"Missing English pivot record for {unit.id}.")
        result = replace(result, legs=pivot.legs, pivot_unit_id=pivot.unit_id)
        if pivot.reason is not None:
            return replace(result, reason=f"English pivot failed: {pivot.reason}", terminology=terms)
        [pivot_leg] = pivot.legs
        assert pivot_leg.protected_payload is not None
        payload = pivot_leg.protected_payload

    masked, replacements, applied = _apply_terms(payload, unit.id, target, terms, plan, route == "pivot")
    leg = _translate_leg(unit.id, "en" if route == "pivot" else source, target, masked,
                         plan.payload_budget, translate, repair=repair)
    result = replace(result, legs=(*result.legs, leg), reason=leg.reason)
    if leg.protected_payload is None:
        return replace(result, terminology=terms)

    text = restore_spans(leg.protected_payload, {**unit.protected_spans, **replacements})
    seen = _term_counts(text, tuple({term.expected_term for term in terms}))
    checks = []
    for term in terms:
        observed = seen[term.expected_term.casefold()]
        checks.append(replace(term, observed_count=observed, applied_count=applied[term.source_term],
                              status="seen" if observed >= term.expected_count else "missing"))
    return replace(result, text=text, terminology=tuple(checks))


def _target_outcome(
    units: tuple[TranslatedUnit, ...], source: str | None, target: str,
) -> tuple[LanguageStatus, str | None]:
    if target not in LANGUAGES:
        return "failed", f"Unsupported target language: {target}."
    if source not in LANGUAGES:
        return "failed", "Set --source-lang to translate an unresolved source language."
    if not units:
        return "failed", "The source chapter has no eligible translation units."

    requested = [unit for unit in units if unit.route in {"direct", "pivot"}]
    if not any(unit.text is not None for unit in (requested or units)):
        return "failed", "No required translation unit succeeded."
    if any(unit.reason is not None or any(term.status == "missing" for term in unit.terminology)
           for unit in units):
        return "completed_with_misses", "Some units failed or missed glossary terms."
    return "completed", None


def translate_plan(
    plan: TranslationPlan, target_languages: Sequence[str], translate: Translate,
) -> TranslationRun:
    """Keep one English leg per unit for the fixed settings of this run."""
    pivots: dict[str, TranslatedUnit] = {}
    targets = []
    source = plan.source_lang
    needs_pivot = source in LANGUAGES and source != "en" and any(
        target in LANGUAGES and target not in {source, "en"} for target in target_languages
    )
    for target in dict.fromkeys(target_languages):
        units = []
        for unit in plan.units:
            pivot = None
            if needs_pivot and target in LANGUAGES and target != source and not unit.literal and unit.status == "ready":
                if target == "en" and _expected_terms(unit, target, plan):
                    units.append(translate_unit(unit, source, target, plan, translate))
                    continue
                if unit.id not in pivots:
                    pivots[unit.id] = translate_unit(unit, source, "en", plan, translate, use_glossary=False)
                pivot = pivots[unit.id]
                if target == "en":
                    units.append(replace(pivot, pivot_unit_id=unit.id))
                    continue
            units.append(translate_unit(unit, source, target, plan, translate, pivot=pivot))

        outcome, reason = _target_outcome(tuple(units), source, target)
        targets.append(TargetTranslation(source, target, outcome, reason, tuple(units)))
    return TranslationRun(tuple(targets), tuple(pivots.values()))


def _structure_issues(
    unit: TranslationUnit, candidate: TranslatedUnit, *, spoken_degrees: bool = False,
) -> tuple[TranslationIssue, ...]:
    issues = []
    expected_slot = {
        "address": asdict(unit.address), "section_id": unit.section_id, "group_id": unit.group_id,
        "source_block_ids": unit.source_block_ids, "parent_id": unit.parent_id, "part": unit.part,
        "join_after": unit.join_after,
    }
    observed_slot = {key: getattr(candidate, key) for key in expected_slot}
    observed_slot["address"] = asdict(candidate.address)
    if expected_slot != observed_slot:
        issues.append(TranslationIssue("address", "The result belongs to a different structural slot.",
                                       expected_slot, observed_slot))

    for leg in candidate.legs:
        check = leg.placeholder_check
        if check is not None and (check.status == "failed" or check.expected != check.observed):
            issues.append(TranslationIssue("placeholders", f"Placeholder counts changed on the "
                                           f"{leg.source_lang}→{leg.target_lang} leg.",
                                           check.expected, check.observed))

    text = candidate.text
    if text is None or candidate.reason is not None:
        if not issues:
            issues.append(TranslationIssue("translation", candidate.reason or "The unit has no translation.",
                                           "Usable translated text", text))
        return tuple(issues)

    source = restore_spans(unit.payload, unit.protected_spans)
    if unit.literal or candidate.route == "reuse":
        if text != source:
            issues.append(TranslationIssue("literal", "Literal or same-language text changed.", source, text))
        return tuple(issues)
    if not text.strip():
        issues.append(TranslationIssue("translation", "The unit returned empty text.", "Nonempty text", text))
    expected_tokens = dict(Counter(TOKEN.findall(source)))
    observed_tokens = dict(Counter(TOKEN.findall(text)))
    if expected_tokens != observed_tokens:
        issues.append(TranslationIssue("placeholders", "Unrestored placeholders reached the translated text.",
                                       expected_tokens, observed_tokens))

    def count_literals(value: str) -> dict[str, int]:
        return dict(Counter(SPOKEN_DEGREE_UNIT.sub("", match[0]) if spoken_degrees else match[0]
                            for match in literal_matches(value)))

    expected_literals = count_literals(source)
    observed_literals = count_literals(text)
    if expected_literals != observed_literals:
        issues.append(TranslationIssue("literals", "Exact quantities, references or inline LaTeX changed.",
                                       expected_literals, observed_literals))
    if unit.address.kind == "heading":
        lines = text.splitlines()
        if any(ATX_HEADING.match(line) or re.fullmatch(r" {0,3}#{1,6}[ \t]*", line) for line in lines) or any(
            SETEXT.fullmatch(line) for line in lines[1:]
        ):
            issues.append(TranslationIssue("heading", "The result introduced Markdown heading syntax.",
                                           "Heading text without a Markdown marker", text))
    return tuple(issues)


def translate_checked_english(
    unit: TranslationUnit, target: str, plan: TranslationPlan, translate: Translate,
    *, preserve_literal_order: bool = False, spoken_degrees: bool = False,
) -> TranslatedUnit:
    """Translate a derived English unit and apply the same literal checks as lesson QA."""
    result = translate_unit(unit, "en", target, plan, translate, use_glossary=False)
    issues = _structure_issues(unit, result, spoken_degrees=spoken_degrees)
    if issues:
        return replace(result, text=None, reason=issues[0].reason)
    if preserve_literal_order and result.legs:
        payload = result.legs[-1].protected_payload or ""
        if TOKEN.findall(unit.payload) != TOKEN.findall(payload):
            return replace(result, text=None, reason="Equation operands changed order in translation.")
    return result


def _retryable(unit: TranslationUnit, source: str | None, target: str,
               issues: tuple[TranslationIssue, ...]) -> bool:
    if unit.status != "ready" or unit.literal or source not in LANGUAGES or target not in LANGUAGES:
        return False
    if source == target:
        return False
    # Provider transport has its own retry budget. A content repair must not
    # repeat an authentication failure or an exhausted transport request.
    return any(issue.check != "translation" or issue.reason.removeprefix("English pivot failed: ") in {
        "Indic-Translate reported a truncated response.", "Indic-Translate returned empty text.",
        "The unit returned empty text.", "The unit has no translation.",
    } for issue in issues)


def _retry_unit(
    unit: TranslationUnit, target: TargetTranslation, plan: TranslationPlan, translate: Translate,
    issues: tuple[TranslationIssue, ...], pivots: dict[str, TranslatedUnit],
    pivot_repairs: dict[str, TranslatedUnit],
) -> TranslatedUnit:
    source = plan.source_lang
    language = target.target_lang
    pivot = None
    shared_english = language == "en" and unit.id in pivots and not _expected_terms(unit, language, plan)
    if source != "en" and (language != "en" or shared_english):
        pivot = pivot_repairs.get(unit.id) or pivots.get(unit.id)
        pivot_issues = _structure_issues(unit, pivot) if pivot is not None else (
            TranslationIssue("coverage", "The English pivot is missing.", unit.id, None),
        )
        if pivot_issues and unit.id not in pivot_repairs and _retryable(unit, source, "en", pivot_issues):
            pivot = translate_unit(unit, source, "en", plan, translate, use_glossary=False,
                                   repair=pivot_issues)
            pivot_issues = _structure_issues(unit, pivot)
            if pivot_issues:
                pivot = replace(pivot, text=None, reason=pivot_issues[0].reason)
            pivot_repairs[unit.id] = pivot
        if pivot is not None and pivot_issues:
            pivot = replace(pivot, reason=pivot_issues[0].reason)
        if language == "en" and pivot is not None:
            return pivot
    return translate_unit(unit, source, language, plan, translate, pivot=pivot, repair=issues)


def _check_unit(
    unit: TranslationUnit, candidates: list[TranslatedUnit], target: TargetTranslation,
    plan: TranslationPlan, translate: Translate, pivots: dict[str, TranslatedUnit],
    pivot_repairs: dict[str, TranslatedUnit],
) -> UnitTranslationQA:
    candidate = candidates[0] if len(candidates) == 1 else None
    issues = _structure_issues(unit, candidate) if candidate is not None else (
        TranslationIssue("coverage", "Expected exactly one result for this unit.", 1, len(candidates)),
    )
    result = UnitTranslationQA(
        unit.id, unit.address, unit.source_block_ids, not issues,
        candidate.text if candidate is not None and not issues else None,
        "unavailable" if issues else "accepted", issues,
        parent_id=unit.parent_id, part=unit.part, join_after=unit.join_after,
        terminology=candidate.terminology if candidate is not None else (),
    )
    if not issues or not _retryable(unit, plan.source_lang, target.target_lang, issues):
        return result

    repaired = _retry_unit(unit, target, plan, translate, issues, pivots, pivot_repairs)
    retry_issues = _structure_issues(unit, repaired)
    # A retry can reuse a successful initial pivot. Its original attempts stay
    # in the translation artifact, while new repair attempts are logged in QA.
    initial_ids = {attempt_id for record in candidates for leg in record.legs for attempt_id in leg.attempt_ids}
    if unit.id in pivots:
        initial_ids.update(attempt_id for leg in pivots[unit.id].legs for attempt_id in leg.attempt_ids)
    retry_ids = tuple(attempt_id for leg in repaired.legs for attempt_id in leg.attempt_ids
                      if attempt_id not in initial_ids)
    return replace(result, eligible=not retry_issues, text=repaired.text if not retry_issues else None,
                   disposition="exhausted" if retry_issues else "recovered",
                   retry_issues=retry_issues, retry_attempt_ids=retry_ids, terminology=repaired.terminology)


def _slot_plan_issue(
    units: list[TranslationUnit], slot_id: str, address: SegmentAddress, section_id: str,
    block: SourceBlock, source: str,
) -> TranslationIssue | None:
    members = [unit.id for unit in units]
    split = any(unit.part is not None for unit in units)
    expected = [f"{slot_id}:p{index:02d}" for index in range(1, len(units) + 1)] if split else [slot_id]
    if members != expected or any(
        unit.address != address or unit.section_id != section_id or unit.group_id != block.id
        or unit.source_block_ids != block.source_block_ids
        or (unit.parent_id, unit.part) != ((slot_id, index) if split else (None, None))
        for index, unit in enumerate(units, 1)
    ):
        return TranslationIssue("plan_coverage", "The plan does not cover the canonical slot.", expected, members)
    try:
        restored = "".join(restore_spans(unit.payload, unit.protected_spans) + unit.join_after for unit in units)
    except ValueError as error:
        return TranslationIssue("plan_source", "The planned placeholders are inconsistent.", source, str(error))
    if restored != source:
        return TranslationIssue("plan_source", "The planned text does not match the canonical slot.", source, restored)
    return None


def _section_status(
    units: list[UnitTranslationQA], gaps: tuple[str, ...],
) -> tuple[LanguageStatus, str | None]:
    misses = bool(gaps) or any(not unit.eligible or any(term.status == "missing" for term in unit.terminology)
                              for unit in units)
    if misses and not any(unit.eligible for unit in units):
        return "failed", "No checked translation is available in this section."
    if misses:
        return "completed_with_misses", "The section has unavailable content or missed glossary terms."
    return "completed", None


def check_structure(
    chapter: SourceChapter, plan: TranslationPlan, translations: TranslationRun, translate: Translate,
) -> TranslationQARun:
    """Check per-slot fidelity, repair invalid units once, and render in canonical order."""
    planned: dict[str, list[TranslationUnit]] = defaultdict(list)
    for unit in plan.units:
        planned[unit.parent_id or unit.id].append(unit)
    known_ids = {unit.id for unit in plan.units}
    pivots = {unit.unit_id: unit for unit in translations.pivots}
    pivot_repairs: dict[str, TranslatedUnit] = {}
    lessons = []
    for target in translations.targets:
        candidates: dict[str, list[TranslatedUnit]] = defaultdict(list)
        for unit in target.units:
            candidates[unit.unit_id].append(unit)
        extras = tuple(TranslationIssue("coverage", "Ignored a result with no planned unit.", None, unit_id)
                       for unit_id in candidates if unit_id not in known_ids)
        sections = []
        overrides = {}
        plain_slots = set()
        for section in chapter.sections:
            checked = []
            gaps = tuple(block.id for block in section.blocks if not block.eligible)
            for block in section.blocks:
                if not block.eligible:
                    continue
                for slot_id, address, source in block_slots(block):
                    members = planned[slot_id]
                    problem = _slot_plan_issue(members, slot_id, address, section.id, block, source)
                    if problem is not None:
                        results = [UnitTranslationQA(slot_id, address, block.source_block_ids, False,
                                                     None, "unavailable", (problem,))]
                    else:
                        results = [_check_unit(unit, candidates[unit.id], target, plan, translate,
                                               pivots, pivot_repairs) for unit in members]
                    checked.extend(results)
                    overrides[slot_id] = "".join(
                        (result.text if result.eligible else f"[Translation unavailable: {result.unit_id}]")
                        + result.join_after for result in results
                    )
                    if any(not result.eligible for result in results) or (
                        target.target_lang != plan.source_lang and any(not unit.literal for unit in members)
                    ):
                        plain_slots.add(slot_id)
            status, reason = _section_status(checked, gaps)
            sections.append(SectionTranslationQA(section.id, status, reason, tuple(checked), gaps))

        accepted = {unit.unit_id for section in sections for unit in section.units if unit.eligible}
        required = {unit.id for unit in plan.units if not unit.literal} or known_ids
        if not accepted & required:
            status, reason = "failed", target.reason or "No required unit passed translation QA."
        elif extras or any(section.status != "completed" for section in sections):
            status, reason = "completed_with_misses", "Some units or sections have unresolved QA findings."
        else:
            status, reason = "completed", None
        qa = TargetTranslationQA(plan.source_lang, target.target_lang, status, reason, tuple(sections), extras)
        lessons.append(CheckedLesson(qa, render_markdown(chapter, overrides, plain_text_units=plain_slots)))
    # Keep English evidence available in memory even when English was not a
    # requested target. Validate each candidate against its original slot.
    english_pivots = []
    for unit in plan.units:
        pivot = pivot_repairs.get(unit.id) or pivots.get(unit.id)
        if pivot is not None and not _structure_issues(unit, pivot):
            english_pivots.append(pivot)
    return TranslationQARun(tuple(lessons), tuple(pivot_repairs.values()), tuple(english_pivots))
