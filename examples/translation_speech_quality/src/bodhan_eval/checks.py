"""Conservative text checks. Spans always index the original input string."""

import difflib
import re
import unicodedata
from collections import Counter

from sacrebleu.metrics import BLEU, CHRF

CATEGORIES = (
    "omission", "addition", "mistranslation", "number", "entity", "format",
    "wrong_language", "speech_omission", "speech_repetition", "garbled_audio",
)
SCRIPTS = {
    "hi": "DEVANAGARI", "mr": "DEVANAGARI", "ne": "DEVANAGARI",
    "sa": "DEVANAGARI", "doi": "DEVANAGARI", "mai": "DEVANAGARI",
    "kok": "DEVANAGARI", "sd": "DEVANAGARI", "bn": "BENGALI",
    "as": "BENGALI", "ta": "TAMIL", "te": "TELUGU", "gu": "GUJARATI",
    "kn": "KANNADA", "ml": "MALAYALAM", "or": "ORIYA", "pa": "GURMUKHI",
    "ur": "ARABIC", "ks": "ARABIC", "brx": "DEVANAGARI", "mni": "MEETEI",
    "sat": "OL CHIKI", "en": "LATIN",
}
VOICES = {
    "hi": "Kavya", "mr": "Anagha", "ne": "Srijana", "sa": "Bharati",
    "doi": "Preeti", "mai": "Vaidehi", "kok": "Anjali", "sd": "Moomal",
    "bn": "Ishita", "as": "Prastuti", "ta": "Anitha", "te": "Sravani",
    "gu": "Dhara", "kn": "Deepika", "ml": "Lakshmi", "or": "Itishree",
    "pa": "Kaur", "ur": "Saba", "ks": "Zoon", "brx": "Gwrbw", "mni": "Thoibi",
    "sat": "Phulmani", "en": "Kavya",
}


def normalized(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    chars = []
    for char in text:
        if char in "\u200b\u200c\u200d\ufeff":
            continue
        if char.isdecimal():
            char = str(unicodedata.decimal(char))
        chars.append(char)
    return " ".join("".join(chars).split())


def flag(category, severity, field, span, evidence, check, confidence=1.0):
    return {"type": category, "severity": severity, "field": field,
            "span": list(span), "evidence": evidence, "check": check,
            "confidence": confidence}


def numeric_spans(text):
    """Unify decimal scripts and grouped thousands, retaining source offsets."""
    found = []
    for match in re.finditer(r"(?<!\w)[+−-]?\d+(?:[.,]\d+)*", text):
        value = normalized(match.group()).replace("−", "-")
        if re.fullmatch(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", value):
            value = value.replace(",", "")
        found.append((value, match.span()))
    return found


def number_flags(source, translation):
    expected = Counter(value for value, _ in numeric_spans(source))
    observed = Counter(value for value, _ in numeric_spans(translation))
    if expected == observed:
        return []
    extra = observed - expected
    missing = expected - observed
    # Spelling a number out is valid localization. It is a review candidate,
    # rather than a confirmed error, unless an inconsistent digit also occurs.
    result = []
    for value, span in numeric_spans(translation):
        if extra[value]:
            result.append(flag("number", "major", "translation", span,
                               {"observed": value, "missing": list(missing.elements())},
                               "number_preservation", 0.98 if missing else 0.65))
            extra[value] -= 1
    for value, span in numeric_spans(source):
        if missing[value]:
            result.append(flag("number", "major", "source", span,
                               {"missing": value, "could_be_spelled_out": True},
                               "number_coverage", 0.55))
            missing[value] -= 1
    return result


def format_signature(text):
    fences = re.findall(r"^\s*```", text, re.MULTILINE)
    math = re.findall(r"(?<!\\)\$\$.*?(?<!\\)\$\$|(?<!\\)\$(?!\$).*?(?<!\\)\$", text, re.DOTALL)
    code = re.findall(r"(?<!`)`([^`\n]+)`(?!`)", text)
    links = re.findall(r"\]\(([^)]+)\)", text)
    table = [len(line.strip().strip("|").split("|")) for line in text.splitlines()
             if "|" in line and len(line.strip().strip("|").split("|")) > 1]
    return {"fences": len(fences), "math": math, "code": code, "links": links, "table": table}


def format_flags(source, translation):
    a, b = format_signature(source), format_signature(translation)
    result = []
    for kind in a:
        if a[kind] != b[kind]:
            result.append(flag("format", "major", "translation", (0, len(translation)),
                               {"component": kind, "expected": a[kind], "observed": b[kind]},
                               "format_integrity", 0.95))
    return result


def script_flags(translation, language):
    expected = SCRIPTS[language]
    letters = [(i, unicodedata.name(c, "")) for i, c in enumerate(translation) if c.isalpha()]
    if len(letters) < 5:
        return []
    ratio = sum(expected in name for _, name in letters) / len(letters)
    if ratio >= 0.2:
        return []
    return [flag("wrong_language", "major", "translation", (0, len(translation)),
                 {"expected_script": expected, "script_ratio": round(ratio, 4),
                  "caveat": "Script is not language identification. Romanized output needs review."},
                 "script_coverage", 0.8)]


def entity_flags(source, translation, glossary):
    """Only enforce caller-specified glossary entries. Never guess transliterations."""
    results = []
    for term, targets in glossary.items():
        if normalized(term) not in normalized(source):
            continue
        targets = [targets] if isinstance(targets, str) else targets
        if not any(normalized(t) in normalized(translation) for t in targets):
            match = re.search(re.escape(term), source, re.IGNORECASE)
            span = match.span() if match else (0, len(source))
            results.append(flag("entity", "major", "source", span,
                                {"term": term, "allowed_targets": targets},
                                "explicit_glossary", 0.9))
    return results


def reference_metrics(reference, translation):
    if not reference:
        return {}
    ref, hyp = normalized(reference), normalized(translation)
    return {"chrf++": round(CHRF(word_order=2).sentence_score(hyp, [ref]).score, 4),
            "bleu": round(BLEU(tokenize="none", effective_order=True).sentence_score(hyp, [ref]).score, 4)}


def edit_alignment(expected, observed):
    """Levenshtein alignment with original expected-string offsets (small text)."""
    a, b = normalized(expected), normalized(observed)
    # SequenceMatcher provides useful local evidence. CER uses an exact DP below.
    changes = []
    for kind, i, j, p, q in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if kind != "equal":
            changes.append({"operation": kind, "expected_span": [i, j],
                            "observed_span": [p, q], "expected": a[i:j], "observed": b[p:q]})
    previous = list(range(len(b) + 1))
    for i, char in enumerate(a, 1):
        current = [i]
        for j, other in enumerate(b, 1):
            current.append(min(current[-1] + 1, previous[j] + 1,
                               previous[j - 1] + (char != other)))
        previous = current
    return {"cer": previous[-1] / max(1, len(a)), "changes": changes,
            "span_space": "normalized_text"}


def speech_text_flags(expected, transcript):
    comparison = edit_alignment(expected, transcript)
    candidates = []
    for change in comparison["changes"]:
        a, b = change["expected"].strip(), change["observed"].strip()
        if change["operation"] == "delete" and len(a) >= 4:
            candidates.append(flag("speech_omission", "major", "translation", (0, len(expected)),
                                   change, "asr_coverage", min(0.85, 0.5 + len(a) / max(1, len(expected)))))
        if change["operation"] == "insert" and len(b) >= 4 and b in normalized(expected):
            candidates.append(flag("speech_repetition", "major", "transcript", (0, len(transcript)),
                                   change, "asr_repetition", 0.8))
    return comparison, candidates


def deterministic_checks(item):
    source, translation, lang = item["source"], item.get("translation"), item["lang"]
    if translation is None:
        return [], {}
    if not translation.strip() and source.strip():
        return [flag("omission", "critical", "translation", (0, 0),
                     {"reason": "empty output for nonempty source"}, "empty_output")], {}
    results = (number_flags(source, translation) + format_flags(source, translation)
               + script_flags(translation, lang)
               + entity_flags(source, translation, item.get("glossary", {})))
    letters = [c for c in source if c.isalpha()]
    if (SCRIPTS[lang] != "LATIN" and len(letters) >= 12
            and all("LATIN" in unicodedata.name(c, "") for c in letters)
            and normalized(source) == normalized(translation)):
        results = [f for f in results if f["check"] != "script_coverage"]
        results.append(flag("wrong_language", "major", "translation", (0, len(translation)),
                            {"reason": "English source was copied unchanged into a requested Indic output"},
                            "untranslated_source", 0.99))
    return results, reference_metrics(item.get("reference"), translation)
