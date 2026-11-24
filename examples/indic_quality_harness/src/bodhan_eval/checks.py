"""Deterministic translation checks.

Every check returns a dict with this shape::

    {
        "category": "number_mismatch",
        "status": "pass" | "fail" | "review",
        "passed": True | False | None,     # None means "needs review"
        "severity": "none" | "low" | "medium" | "high",
        "evidence": {...},
    }

``fail`` is reserved for errors the check can prove (a number that is in the
source but nowhere in the translation, a URL that changed, a Markdown link
that disappeared). Anything a lexicon or heuristic cannot settle becomes
``review``, so it is visible without inflating the failure count.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher

from bodhan_eval.lexicons import (
    DAY_PERIODS,
    MONTHS,
    NUMBER_WORDS,
    PLACES,
    SCALE_WORDS,
    UNITS,
    WEEKDAYS,
    find_concept_spans,
    find_concepts,
)
from bodhan_eval.metrics import chrf
from bodhan_eval.normalize import normalize_text
from bodhan_eval.speech_checks import tokenize

# chrF below this against an independent reference is flagged for review.
REFERENCE_CHRF_THRESHOLD = 0.45


def make_check(
    category: str,
    status: str,
    severity: str,
    evidence: dict,
    **extra,
) -> dict:
    passed = {"pass": True, "fail": False, "review": None}[status]
    return {
        "category": category,
        "status": status,
        "passed": passed,
        "severity": "none" if status == "pass" else severity,
        "evidence": evidence,
        **extra,
    }


def digits_to_ascii(text: str) -> str:
    """Convert any Unicode decimal digit to 0-9 without touching whitespace."""
    out = []
    for char in text:
        try:
            out.append(str(unicodedata.decimal(char)))
        except (TypeError, ValueError):
            out.append(char)
    return "".join(out)


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"(?<!\w)(\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)")


def extract_numbers(text: str) -> list[str]:
    """Extract numeric values, including decimal numbers (raw strings)."""
    return re.findall(r"(?<!\w)\d+(?:[.,]\d+)?", text)


def _canonical(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def extract_quantities(text: str) -> list[str]:
    """Extract numbers as canonical values, applying scale words.

    "1 million", "10 लाख", "10,00,000" and "১০ লক্ষ" all become "1000000".
    Thousands separators (Western and Indian grouping) are removed.
    """
    text = normalize_text(text)
    quantities = []

    for match in _NUMBER_RE.finditer(text):
        try:
            value = Decimal(match.group().replace(",", ""))
        except InvalidOperation:
            continue

        following = text[match.end():].lstrip(" -")
        next_token = re.split(r"[\s,.;:!?()]", following, maxsplit=1)[0].lower()

        if next_token:
            for scale_word, multiplier in SCALE_WORDS.items():
                if next_token.startswith(scale_word.lower()):
                    value *= multiplier
                    break

        quantities.append(_canonical(value))

    return quantities


def _number_words_present(text: str, language: str | None) -> set[str]:
    """Return the numeric values written as words in ``text``."""
    languages = [language] if language in NUMBER_WORDS else list(NUMBER_WORDS)
    found = set()

    for token in tokenize(text):
        for lang in languages:
            for word, value in NUMBER_WORDS[lang].items():
                if token == word or (len(word) >= 4 and token.startswith(word)):
                    found.add(value)

    return found


def check_numbers(
    source_text: str,
    translated_text: str,
    language: str | None = None,
) -> dict:
    """Compare the multiset of numeric values in source and translation.

    Order is ignored (word order differs between languages) and scale words
    are applied. A difference that is fully explained by a number written
    as a word (for example "10 AM" -> "दस बजे") becomes ``review``.
    """
    source_numbers = extract_quantities(source_text)
    translated_numbers = extract_quantities(translated_text)

    source_counts = Counter(source_numbers)
    translated_counts = Counter(translated_numbers)

    missing = sorted((source_counts - translated_counts).elements())
    extra = sorted((translated_counts - source_counts).elements())

    evidence = {
        "source_numbers": source_numbers,
        "translated_numbers": translated_numbers,
        "missing_in_translation": missing,
        "extra_in_translation": extra,
    }

    if not missing and not extra:
        return make_check("number_mismatch", "pass", "none", evidence)

    words_in_translation = _number_words_present(translated_text, language)
    words_in_source = _number_words_present(source_text, "en")

    unexplained_missing = [n for n in missing if n not in words_in_translation]
    unexplained_extra = [n for n in extra if n not in words_in_source]

    if not unexplained_missing and not unexplained_extra:
        evidence["note"] = "Difference explained by a number written as a word."
        return make_check("number_mismatch", "review", "low", evidence)

    return make_check("number_mismatch", "fail", "high", evidence)


# ---------------------------------------------------------------------------
# Formatting: Markdown markers, Markdown structure, LaTeX
# ---------------------------------------------------------------------------

def check_formatting(source_text: str, translated_text: str) -> dict:
    """Count emphasis / code / math markers on both sides."""
    markers = ["**", "__", "```", "$$", "$"]

    missing = []

    for marker in markers:
        source_count = source_text.count(marker)
        translated_count = translated_text.count(marker)

        if source_count != translated_count:
            missing.append({
                "marker": marker,
                "source_count": source_count,
                "translated_count": translated_count,
            })

    return make_check(
        "formatting_mismatch",
        "fail" if missing else "pass",
        "medium",
        {"mismatches": missing},
    )


_FENCE_RE = re.compile(r"```[^\n]*\n?(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"!?\[([^\]]*)\]\(\s*([^)\s]+)[^)]*\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+\S", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*+]\s+\S", re.MULTILINE)
_ORDERED_RE = re.compile(r"^\s*\d+[.)]\s+\S", re.MULTILINE)
_QUOTE_RE = re.compile(r"^\s*>", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)


def _strip_code(text: str) -> str:
    text = _FENCE_RE.sub(" ", text)
    return _INLINE_CODE_RE.sub(" ", text)


def markdown_features(text: str) -> dict:
    text = digits_to_ascii(unicodedata.normalize("NFC", text))
    without_fences = _FENCE_RE.sub(" ", text)
    return {
        "headings": [len(h) for h in _HEADING_RE.findall(without_fences)],
        "bullets": len(_BULLET_RE.findall(without_fences)),
        "ordered_items": len(_ORDERED_RE.findall(without_fences)),
        "blockquotes": len(_QUOTE_RE.findall(without_fences)),
        "table_rows": len(_TABLE_ROW_RE.findall(without_fences)),
        "link_urls": sorted(url for _, url in _LINK_RE.findall(without_fences)),
        "inline_code": sorted(c.strip() for c in _INLINE_CODE_RE.findall(without_fences)),
        "code_blocks": [block.strip() for block in _FENCE_RE.findall(text)],
    }


def has_markdown(text: str) -> bool:
    features = markdown_features(text)
    return any(features.values())


def check_markdown_structure(source_text: str, translated_text: str) -> dict:
    """Compare Markdown structure. Code and URLs must survive verbatim."""
    source = markdown_features(source_text)
    translated = markdown_features(translated_text)

    mismatches = {
        key: {"source": source[key], "translation": translated[key]}
        for key in source
        if source[key] != translated[key]
    }

    verbatim_broken = any(
        key in mismatches for key in ("link_urls", "inline_code", "code_blocks")
    )

    return make_check(
        "markdown_structure",
        "fail" if mismatches else "pass",
        "high" if verbatim_broken else "medium",
        {"mismatches": mismatches},
    )


_MATH_PATTERNS = [
    re.compile(r"\$\$(.+?)\$\$", re.DOTALL),
    re.compile(r"\\\[(.+?)\\\]", re.DOTALL),
    re.compile(r"\\\((.+?)\\\)", re.DOTALL),
    re.compile(r"(?<![\\$])\$(?!\$)([^$\n]+?)(?<!\\)\$"),
]
_LATEX_COMMAND_RE = re.compile(r"\\[A-Za-z]+")
_ENV_RE = re.compile(r"\\(begin|end)\{([^}]*)\}")


def extract_math(text: str) -> list[str]:
    """Extract math segments ($..$, $$..$$, \\(..\\), \\[..\\]), normalised."""
    text = _strip_code(text)
    segments = []

    for pattern in _MATH_PATTERNS:
        for match in pattern.finditer(text):
            segments.append(normalize_text(match.group(1)))
        text = pattern.sub(" ", text)

    return segments


def has_latex(text: str) -> bool:
    return bool(extract_math(text) or _LATEX_COMMAND_RE.search(_strip_code(text)))


def _balanced(text: str) -> bool:
    depth = 0
    for index, char in enumerate(text):
        if char == "{" and (index == 0 or text[index - 1] != "\\"):
            depth += 1
        elif char == "}" and (index == 0 or text[index - 1] != "\\"):
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def check_latex(source_text: str, translated_text: str) -> dict:
    """Math must be copied verbatim; commands, braces and environments kept."""
    source_math = extract_math(source_text)
    translated_math = extract_math(translated_text)

    missing_math = list((Counter(source_math) - Counter(translated_math)).elements())
    extra_math = list((Counter(translated_math) - Counter(source_math)).elements())

    source_commands = Counter(_LATEX_COMMAND_RE.findall(_strip_code(source_text)))
    translated_commands = Counter(_LATEX_COMMAND_RE.findall(_strip_code(translated_text)))

    unbalanced = [segment for segment in translated_math if not _balanced(segment)]

    source_envs = Counter(_ENV_RE.findall(source_text))
    translated_envs = Counter(_ENV_RE.findall(translated_text))

    evidence = {
        "missing_math": missing_math,
        "altered_or_extra_math": extra_math,
        "missing_commands": sorted((source_commands - translated_commands).elements()),
        "extra_commands": sorted((translated_commands - source_commands).elements()),
        "unbalanced_segments": unbalanced,
        "environment_mismatch": source_envs != translated_envs,
    }

    failed = any([
        missing_math,
        extra_math,
        evidence["missing_commands"],
        evidence["extra_commands"],
        unbalanced,
        evidence["environment_mismatch"],
    ])

    return make_check("latex_integrity", "fail" if failed else "pass", "high", evidence)


# ---------------------------------------------------------------------------
# Dates, times and units
# ---------------------------------------------------------------------------

_TIME_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")


def _compare_concepts(
    label: str,
    expected: list[str],
    found: list[str],
    concepts: dict[str, dict],
) -> tuple[list[dict], list[dict]]:
    """Return (contradictions, unverified) for a concept lexicon.

    A concept that is missing while a *different* concept of the same group
    is present in the translation is a contradiction (hard failure). A
    concept that is simply missing is unverified (review): the translation
    may use a spelling the lexicon does not know.
    """
    contradictions = []
    unverified = []
    found_set = set(found)
    expected_set = set(expected)

    for concept_id in sorted(expected_set):
        if concept_id in found_set:
            continue

        group = concepts[concept_id]["group"]
        rivals = sorted(
            other for other in found_set - expected_set
            if concepts[other]["group"] == group
        )

        entry = {"type": label, "expected": concept_id}
        if rivals:
            entry["found_instead"] = rivals
            contradictions.append(entry)
        else:
            unverified.append(entry)

    return contradictions, unverified


def check_dates_times(
    source_text: str,
    translated_text: str,
    language: str | None,
) -> dict | None:
    """Check clock times, AM/PM, month names and weekdays.

    Returns ``None`` when the source has no date or time expression.
    Numeric parts of dates (day, year) are covered by :func:`check_numbers`.
    """
    source = digits_to_ascii(unicodedata.normalize("NFC", source_text))
    translated = digits_to_ascii(unicodedata.normalize("NFC", translated_text))

    times = [f"{int(h)}:{m}" for h, m in _TIME_RE.findall(source)]
    lexicons = [
        ("day_period", DAY_PERIODS),
        ("month", MONTHS),
        ("weekday", WEEKDAYS),
    ]
    expected = {
        label: find_concepts(source, concepts, "en")
        for label, concepts in lexicons
    }

    if not times and not any(expected.values()):
        return None

    contradictions = []
    unverified = []

    translated_times = {f"{int(h)}:{m}" for h, m in re.findall(r"(?<!\d)(\d{1,2})[:.](\d{2})(?!\d)", translated)}
    for time in times:
        if time not in translated_times:
            contradictions.append({"type": "time", "expected": time})

    for label, concepts in lexicons:
        if not expected[label]:
            continue
        found = find_concepts(translated, concepts, language)
        c, u = _compare_concepts(label, expected[label], found, concepts)
        contradictions.extend(c)
        unverified.extend(u)

    evidence = {
        "times": times,
        **{label: values for label, values in expected.items() if values},
        "contradictions": contradictions,
        "unverified": unverified,
    }

    if contradictions:
        return make_check("date_time_mismatch", "fail", "high", evidence)
    if unverified:
        return make_check("date_time_mismatch", "review", "low", evidence)
    return make_check("date_time_mismatch", "pass", "none", evidence)


_QUANTITY_BEFORE_RE = re.compile(
    r"(?:\d[\d,.]*|\b(?:" + "|".join(NUMBER_WORDS["en"]) + r"|per|"
    + "|".join(word for word in SCALE_WORDS if word.isascii()) + r"))\s*-?\s*$",
    re.IGNORECASE,
)


def source_units(source_text: str) -> list[str]:
    """Units in an English source that are attached to a quantity."""
    text = digits_to_ascii(unicodedata.normalize("NFC", source_text))
    units = []

    for start, _, unit in find_concept_spans(text, UNITS, "en"):
        if _QUANTITY_BEFORE_RE.search(text[max(0, start - 30):start]):
            units.append(unit)

    return units


def check_units(
    source_text: str,
    translated_text: str,
    language: str | None,
) -> dict | None:
    """Every unit attached to a quantity in the source must survive.

    A *different* unit of the same dimension (km -> metre, MB -> GB,
    minutes -> hours) is a failure. A unit the lexicon cannot find is review.
    """
    expected = source_units(source_text)
    if not expected:
        return None

    found = find_concepts(translated_text, UNITS, language)
    contradictions, unverified = _compare_concepts("unit", expected, found, UNITS)

    evidence = {
        "source_units": expected,
        "translated_units": found,
        "contradictions": contradictions,
        "unverified": unverified,
    }

    if contradictions:
        return make_check("unit_mismatch", "fail", "high", evidence)
    if unverified:
        return make_check("unit_mismatch", "review", "low", evidence)
    return make_check("unit_mismatch", "pass", "none", evidence)


# ---------------------------------------------------------------------------
# Entities and terminology
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s)\]]+")
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#]*(?:[.-][A-Za-z0-9+#]+)*")
_SENTENCE_START_RE = re.compile(r"(?:^|[.!?:|>\n])[\s#*\-+>|]*$")


def _blank(text: str, pattern: re.Pattern) -> str:
    return pattern.sub(lambda m: " " * len(m.group()), text)


def _prose_only(text: str) -> str:
    """Blank out code, math, URLs and e-mails, keeping character offsets."""
    for pattern in [_FENCE_RE, _INLINE_CODE_RE, *_MATH_PATTERNS, _URL_RE, _EMAIL_RE]:
        text = _blank(text, pattern)
    # Link targets: "[label](target)" -> keep the label only.
    text = re.sub(r"\]\([^)]*\)", lambda m: " " * len(m.group()), text)
    text = re.sub(r"\\[A-Za-z]+", lambda m: " " * len(m.group()), text)
    return text


def _strip_trailing_punctuation(value: str) -> str:
    return value.rstrip(".,;:!?")


def extract_verbatim_entities(text: str) -> list[str]:
    """E-mail addresses and URLs: these must be copied unchanged."""
    text = unicodedata.normalize("NFC", text)
    return [_strip_trailing_punctuation(v) for v in _EMAIL_RE.findall(text) + _URL_RE.findall(text)]


def _lexicon_aliases() -> set[str]:
    aliases = set()
    for concepts in (UNITS, MONTHS, WEEKDAYS, DAY_PERIODS, PLACES):
        for spec in concepts.values():
            aliases.update(alias.lower() for alias in spec.get("en", []))
    return aliases


_KNOWN_ALIASES = _lexicon_aliases()


def extract_latin_entities(text: str) -> list[str]:
    """Latin-script names, brands and acronyms (Android, iOS, PDF, Wi-Fi).

    Heuristic: tokens with an internal capital or all capitals, and
    capitalised tokens that do not start a sentence. Units, months,
    weekdays, AM/PM and known places are excluded (they have their own
    checks), as are e-mail addresses and URLs.
    """
    text = _prose_only(unicodedata.normalize("NFC", text))

    # Blank out multi-word lexicon names ("New Delhi") so their parts are
    # not reported as separate names. Lengths are kept so that the
    # sentence-start detection below still sees the original layout.
    for start, end, _ in find_concept_spans(text, PLACES, "en"):
        text = text[:start] + " " * (end - start) + text[end:]

    entities = []
    for match in _LATIN_TOKEN_RE.finditer(text):
        token = match.group()
        if token.lower() in _KNOWN_ALIASES or token == "I":
            continue

        has_inner_capital = any(ch.isupper() for ch in token[1:])
        is_capitalised = token[0].isupper()
        sentence_initial = bool(_SENTENCE_START_RE.search(text[:match.start()]))

        if has_inner_capital or (is_capitalised and not sentence_initial):
            entities.append(token)

    return entities


def check_entities(
    source_text: str,
    translated_text: str,
    language: str | None,
) -> dict | None:
    """Check e-mails, URLs, known places and Latin-script names.

    * E-mail / URL not copied verbatim -> fail.
    * Known place missing while another known place appears -> fail.
    * Latin name missing while an unexpected Latin name appears -> fail
      (substitution, for example Android -> Windows).
    * Latin name missing with no rival -> review (it was probably
      transliterated, which this check cannot verify).
    """
    translated = unicodedata.normalize("NFC", translated_text)
    translated_lower = translated.lower()

    verbatim = extract_verbatim_entities(source_text)
    latin = extract_latin_entities(source_text)
    places = find_concepts(source_text, PLACES, "en")

    if not verbatim and not latin and not places:
        return None

    contradictions = []
    unverified = []

    for value in verbatim:
        if value not in translated:
            contradictions.append({"type": "verbatim", "expected": value})

    translated_latin = {e.lower() for e in extract_latin_entities(translated)}
    translated_latin |= {
        t.lower() for t in _LATIN_TOKEN_RE.findall(_prose_only(translated))
        if t.lower() not in _KNOWN_ALIASES and len(t) > 1
    }
    source_latin = {e.lower() for e in latin}
    unexpected_latin = sorted(translated_latin - source_latin - {v.lower() for v in verbatim})

    for entity in latin:
        if entity.lower() in translated_lower:
            continue
        entry = {"type": "name", "expected": entity}
        if unexpected_latin:
            entry["found_instead"] = unexpected_latin
            contradictions.append(entry)
        else:
            unverified.append(entry)

    if places:
        found_places = find_concepts(translated, PLACES, language)
        c, u = _compare_concepts("place", places, found_places, PLACES)
        contradictions.extend(c)
        unverified.extend(u)

    evidence = {
        "verbatim_entities": verbatim,
        "latin_entities": latin,
        "places": places,
        "contradictions": contradictions,
        "unverified": unverified,
    }

    if contradictions:
        return make_check("entity_mismatch", "fail", "high", evidence)
    if unverified:
        return make_check("entity_mismatch", "review", "low", evidence)
    return make_check("entity_mismatch", "pass", "none", evidence)


def check_terminology(
    source_text: str,
    translated_text: str,
    glossary: dict | None,
) -> dict | None:
    """Enforce a per-record glossary: ``{"source term": ["allowed", "forms"]}``."""
    if not glossary:
        return None

    translated = unicodedata.normalize("NFC", translated_text)
    applicable = {}
    violations = []

    for term, forms in glossary.items():
        if not re.search(rf"(?<!\w){re.escape(term)}(?!\w)", source_text, re.IGNORECASE):
            continue

        allowed = [forms] if isinstance(forms, str) else list(forms)
        allowed = [unicodedata.normalize("NFC", f) for f in allowed]
        applicable[term] = allowed

        if not any(form in translated for form in allowed):
            violations.append({"term": term, "allowed": allowed})

    if not applicable:
        return None

    return make_check(
        "terminology_mismatch",
        "fail" if violations else "pass",
        "medium",
        {"glossary_terms": applicable, "violations": violations},
    )


# ---------------------------------------------------------------------------
# Reference-based comparison
# ---------------------------------------------------------------------------

def check_reference(
    reference_text: str,
    candidate_text: str,
    threshold: float = REFERENCE_CHRF_THRESHOLD,
) -> dict:
    """chrF of the candidate against an *independent* reference translation.

    A low score is a ``review`` signal, not a failure: a correct translation
    can be phrased differently from the reference.
    """
    score = chrf(candidate_text, reference_text)
    evidence = {"chrf": score, "threshold": threshold}

    if normalize_text(reference_text) == normalize_text(candidate_text):
        evidence["note"] = (
            "Reference is identical to the candidate. Make sure the reference "
            "was not produced by the system under test."
        )

    return make_check(
        "reference_chrf",
        "pass" if score >= threshold else "review",
        "medium",
        evidence,
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_deterministic_checks(record: dict) -> list[dict]:
    translated_text = record.get("translation")

    if not translated_text:
        return []

    raw_source = record["source_text"]
    raw_translation = translated_text
    language = record.get("language")

    source_text = normalize_text(raw_source)
    translated_text = normalize_text(raw_translation)

    checks = [
        check_numbers(source_text, translated_text, language),
        check_formatting(source_text, translated_text),
    ]

    # Structure-aware checks need the raw text (line breaks matter).
    if has_markdown(raw_source):
        checks.append(check_markdown_structure(raw_source, raw_translation))

    if has_latex(raw_source):
        checks.append(check_latex(raw_source, raw_translation))

    optional = [
        check_dates_times(source_text, translated_text, language),
        check_units(source_text, translated_text, language),
        check_entities(raw_source, raw_translation, language),
        check_terminology(raw_source, raw_translation, record.get("glossary")),
    ]
    checks.extend(check for check in optional if check is not None)

    reference = record.get("reference_translation")
    if reference:
        checks.append(check_reference(reference, raw_translation))

    return checks


def _classify_replacement(
    old_tokens: list[str],
    new_tokens: list[str],
    language: str | None,
) -> str | None:
    """Name the kind of substitution between a reference and a candidate."""
    old_are_numbers = all(token.isdigit() for token in old_tokens)
    new_are_numbers = all(token.isdigit() for token in new_tokens)

    # Numeric substitutions are already handled by check_numbers. That
    # includes numbers carrying a suffix ("৭টি" -> "999টি"): same non-digit
    # remainder, different digits.
    if old_are_numbers and new_are_numbers:
        return None
    old_rest = [re.sub(r"\d+", "#", token) for token in old_tokens]
    new_rest = [re.sub(r"\d+", "#", token) for token in new_tokens]
    if old_rest == new_rest and any("#" in token for token in old_rest):
        return None

    old_text = " ".join(old_tokens)
    new_text = " ".join(new_tokens)

    def concepts(text: str, lexicon: dict) -> list[str]:
        # tokenize() lower-cases, but aliases such as "MB" or "Delhi" are
        # case-sensitive, so try the common casings too.
        for variant in (text, text.upper(), text.title()):
            found = find_concepts(variant, lexicon, language)
            if found:
                return found
        return []

    if concepts(old_text, UNITS) != concepts(new_text, UNITS):
        return "unit_mismatch"

    if concepts(old_text, PLACES) != concepts(new_text, PLACES):
        return "entity_mismatch"

    is_latin = any(re.search(r"[A-Za-z]", token) for token in old_tokens + new_tokens)
    if is_latin:
        return "entity_mismatch"

    return "reference_mismatch"


def check_against_reference(
    reference_text: str,
    candidate_text: str,
    language: str | None = None,
) -> list[dict]:
    """Token-level diff against a known clean reference.

    Only meaningful when the reference is the *same* translation before a
    controlled change (calibration). Against an independent human reference
    use :func:`check_reference`, because paraphrases would all be flagged.
    """
    reference_tokens = tokenize(reference_text)
    candidate_tokens = tokenize(candidate_text)

    matcher = SequenceMatcher(
        a=reference_tokens,
        b=candidate_tokens,
        autojunk=False,
    )

    findings = []

    for operation, ref_start, ref_end, cand_start, cand_end in matcher.get_opcodes():
        if operation == "delete":
            findings.append({
                "category": "omission",
                "passed": False,
                "severity": "medium",
                "evidence": {
                    "missing_tokens": reference_tokens[ref_start:ref_end],
                },
            })

        elif operation == "insert":
            inserted = candidate_tokens[cand_start:cand_end]
            previous = (
                reference_tokens[ref_start - 1]
                if ref_start > 0 else None
            )
            following = (
                reference_tokens[ref_start]
                if ref_start < len(reference_tokens) else None
            )

            is_repetition = all(
                token == previous or token == following
                for token in inserted
            )

            findings.append({
                "category": "repetition" if is_repetition else "addition",
                "passed": False,
                "severity": "medium",
                "evidence": {
                    "extra_tokens": inserted,
                },
            })

        elif operation == "replace":
            old_tokens = reference_tokens[ref_start:ref_end]
            new_tokens = candidate_tokens[cand_start:cand_end]

            category = _classify_replacement(old_tokens, new_tokens, language)
            if category is None:
                continue

            findings.append({
                "category": category,
                "passed": False,
                "severity": "medium",
                "evidence": {
                    "reference_tokens": old_tokens,
                    "candidate_tokens": new_tokens,
                },
            })

    return findings
