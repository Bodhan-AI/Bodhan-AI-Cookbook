"""Conservative deterministic checks; spans always refer to original strings."""

import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

SCRIPTS = {
    "hi": "DEVANAGARI",
    "mr": "DEVANAGARI",
    "ne": "DEVANAGARI",
    "sa": "DEVANAGARI",
    "doi": "DEVANAGARI",
    "mai": "DEVANAGARI",
    "kok": "DEVANAGARI",
    "bn": "BENGALI",
    "as": "BENGALI",
    "ta": "TAMIL",
    "te": "TELUGU",
    "kn": "KANNADA",
    "ml": "MALAYALAM",
    "gu": "GUJARATI",
    "or": "ORIYA",
    "pa": "GURMUKHI",
    "ur": "ARABIC",
    "ks": "ARABIC",
    "sd": "DEVANAGARI",
    "brx": "DEVANAGARI",
    "mni": "MEETEI",
    "sat": "OL CHIKI",
    "en": "LATIN",
}


def normalize(text):
    text = unicodedata.normalize("NFC", text)
    return " ".join("".join(str(unicodedata.decimal(c)) if c.isdecimal() else c for c in text).casefold().split())


def flag(category, field, span, evidence, check, severity="major"):
    return {
        "category": category,
        "severity": severity,
        "span_field": field,
        "span": list(span),
        "evidence": evidence,
        "check": check,
        "confidence": "deterministic",
    }


def numbers(text):
    # Match original text, preserving code-point offsets even for non-ASCII digits.
    return [(normalize(m.group()), m.span()) for m in re.finditer(r"\d+(?:[.,]\d+)*", text)]


def number_check(source, translation):
    src, dst = numbers(source), numbers(translation)
    remaining = Counter(n for n, _ in src)
    flags, unexpected = [], []
    for value, span in dst:
        if remaining[value]:
            remaining[value] -= 1
        else:
            unexpected.append((value, span))
    # Missing numbers are source-localized. Do not invent a target span.
    missing = remaining.copy()
    missing_spans = []
    for value, span in src:
        if missing[value]:
            missing[value] -= 1
            missing_spans.append(span)
    # Pair changed quantities once instead of counting one substitution as both
    # an unexpected target number and a missing source number.
    for _, span in unexpected:
        evidence = {"unexpected": translation[slice(*span)], "source_numbers": [n for n, _ in src]}
        if missing_spans:
            missing_span = missing_spans.pop(0)
            evidence.update({"missing_source_number": source[slice(*missing_span)], "source_span": list(missing_span)})
        flags.append(flag("number", "translation", span, evidence, "numbers"))
    for span in missing_spans:
        flags.append(flag("number", "source", span, {"missing_number": source[slice(*span)]}, "numbers"))
    return flags


def format_check(source, translation):
    flags = []
    patterns = {
        "code_fence": r"```",
        "inline_math": r"(?<![\\$])\$(?!\$)",
        "display_math": r"(?<!\\)\$\$",
        "display_math_open": r"\\\[",
        "display_math_close": r"\\\]",
    }
    for name, pattern in patterns.items():
        before = list(re.finditer(pattern, source))
        after = list(re.finditer(pattern, translation))
        if len(before) != len(after):
            field = "translation" if after else "source"
            matches = after or before
            flags.append(flag("format", field, matches[0].span(), {"structure": name, "expected_count": len(before), "actual_count": len(after)}, "format"))
    # URLs and LaTeX commands are literal protected structures, not translated prose.
    for match in re.finditer(r"https?://[^\s)]+|\\[A-Za-z]+", source):
        if match.group() not in translation:
            flags.append(flag("format", "source", match.span(), {"missing_literal": match.group()}, "format"))
    for pattern in (r"(?<!\\)\*\*", r"(?<!\\)__"):
        before, after = list(re.finditer(pattern, source)), list(re.finditer(pattern, translation))
        if len(before) % 2 == 0 and len(after) % 2 == 1:
            flags.append(flag("format", "translation", after[-1].span(), {"reason": "unpaired emphasis delimiter"}, "format"))
    source_tables, target_tables = tables(source), tables(translation)
    for index, table in enumerate(source_tables):
        if index >= len(target_tables):
            flags.append(flag("format", "source", table[0][1], {"reason": "Markdown table structure missing"}, "format"))
        else:
            target = target_tables[index]
            if [row[0] for row in table] != [row[0] for row in target]:
                flags.append(
                    flag(
                        "format",
                        "translation",
                        target[0][1],
                        {"source_table_columns": [r[0] for r in table], "target_table_columns": [r[0] for r in target]},
                        "format",
                    )
                )
    return flags


def tables(text):
    """Recognize ordinary pipe tables; escaped pipes do not create columns."""
    lines, offset = [], 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip().strip("|")
        cells = re.split(r"(?<!\\)\|", stripped)
        separator = len(cells) > 1 and all(re.fullmatch(r"\s*:?-{3,}:?\s*", cell) for cell in cells)
        lines.append((len(cells), (offset, offset + len(line.rstrip("\r\n"))), separator, "|" in line))
        offset += len(line)
    found = []
    for index, line in enumerate(lines):
        if line[2] and index and lines[index - 1][3]:
            rows = lines[index - 1 : index + 1]
            for subsequent in lines[index + 1 :]:
                if not subsequent[3]:
                    break
                rows.append(subsequent)
            found.append(rows)
    return found


def term_check(item):
    flags = []
    for source_term, expected in item.get("terms", {}).items():
        if source_term in item["source"] and normalize(expected) not in normalize(item["translation"]):
            start = item["source"].index(source_term)
            flags.append(flag("entity", "source", (start, start + len(source_term)), {"source_term": source_term, "expected_target_term": expected}, "terms"))
    return flags


def script_check(translation, lang):
    expected = SCRIPTS.get(lang)
    letters = [c for c in translation if unicodedata.category(c).startswith("L")]
    if expected and len(letters) >= 12:
        matching = sum(expected in unicodedata.name(c, "") for c in letters)
        if matching == 0:
            return [
                flag(
                    "wrong_language",
                    "translation",
                    (0, len(translation)),
                    {"expected_script": expected, "matching_letters": 0, "note": "Script mismatch only; cannot identify language within a shared script."},
                    "script",
                )
            ]
    return []


def alignment(expected, actual):
    """Token edit measurements; disagreement alone cannot establish system error."""
    left = list(re.finditer(r"\S+", expected))
    right = list(re.finditer(r"\S+", actual))
    matcher = SequenceMatcher(None, [normalize(m.group()) for m in left], [normalize(m.group()) for m in right], autojunk=False)
    changes = []
    for op, a, b, c, d in matcher.get_opcodes():
        if op == "equal":
            continue
        changes.append(
            {
                "operation": op,
                "expected_span": [left[a].start(), left[b - 1].end()] if b > a else None,
                "actual_span": [right[c].start(), right[d - 1].end()] if d > c else None,
            }
        )
    return {"token_similarity": matcher.ratio(), "differences": changes, "interpretation": "checker_disagreement_requires_corroboration"}


def evaluate(item):
    text = item.get("translation")
    if not text or not text.strip():
        if isinstance(text, str):
            return [flag("omission", "source", (0, len(item["source"])), {"reason": "Supplied translation is empty"}, "empty_output")], {
                "translation": "completed_empty_output"
            }
        return [], {"translation": "missing"}
    flags = number_check(item["source"], text) + format_check(item["source"], text)
    flags += term_check(item) + script_check(text, item["lang"])
    checks = {name: "completed" for name in ("numbers", "format", "terms", "script")}
    if item.get("reference"):
        checks["reference_alignment"] = alignment(item["reference"], text)
        checks["reference_character_f1"] = character_f1(item["reference"], text)
    if item.get("transcript"):
        checks["speech_alignment"] = alignment(text, item["transcript"])
    return flags, checks


def character_f1(reference, output):
    """Whitespace-insensitive normalized character bigram F1 (not chrF++)."""

    def grams(value):
        value = "".join(normalize(value).split())
        return Counter(value[i : i + 2] for i in range(max(1, len(value) - 1))) if value else Counter()

    expected, actual = grams(reference), grams(output)
    hits = sum((expected & actual).values())
    total = sum(expected.values()) + sum(actual.values())
    return {
        "score": 2 * hits / total if total else 1.0,
        "metric": "normalized_character_bigram_F1",
        "interpretation": "reference similarity only; does not prove semantic correctness",
    }
