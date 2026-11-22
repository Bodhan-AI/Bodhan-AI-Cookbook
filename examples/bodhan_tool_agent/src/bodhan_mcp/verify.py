"""Deterministic quality checks: text normalisation, CER, repetition/omission, translation structure."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

from rapidfuzz.distance import Levenshtein

from .langs import LANG_SCRIPT, script_counts

_ZW = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"), None)


def normalize(text: str, drop_digits: bool = False) -> str:
    t = unicodedata.normalize("NFKC", text).translate(_ZW).lower()
    out = []
    for ch in t:
        cat = unicodedata.category(ch)
        if cat == "Nd":
            if not drop_digits:
                out.append(str(unicodedata.decimal(ch)))
            continue
        out.append(" " if cat[0] in "PSZC" else ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def cer(ref: str, hyp: str) -> float:
    a = normalize(ref, drop_digits=True).replace(" ", "")
    b = normalize(hyp, drop_digits=True).replace(" ", "")
    if not a:
        return 0.0 if not b else 1.0
    return min(1.0, Levenshtein.distance(a, b) / len(a))


def find_repetitions(ref: str, hyp: str, min_n: int = 2, max_n: int = 12) -> list[str]:
    """Phrases that the audio repeats back-to-back but the script does not."""
    h = normalize(hyp, drop_digits=True).split()
    r = " ".join(normalize(ref, drop_digits=True).split())
    found: list[str] = []
    for n in range(max_n, min_n - 1, -1):
        for i in range(0, len(h) - 2 * n + 1):
            if h[i:i + n] == h[i + n:i + 2 * n]:
                phrase = " ".join(h[i:i + n])
                if f"{phrase} {phrase}" not in r and phrase not in found and \
                        not any(phrase in f for f in found):
                    found.append(phrase)
    return found[:5]


def find_omissions(ref: str, hyp: str, min_tokens: int = 4) -> list[str]:
    """Runs of script words that never appear in the audio transcript."""
    a = normalize(ref, drop_digits=True).split()
    b = normalize(hyp, drop_digits=True).split()
    if not a:
        return []
    spans = []
    for tag, i1, i2, j1, j2 in Levenshtein.opcodes(a, b).as_list():
        if tag == "delete" and i2 - i1 >= min_tokens:
            spans.append(" ".join(a[i1:i2]))
    return spans[:5]


def speech_verdict(expected: str, heard: str, cer_threshold: float, audio_seconds: float) -> dict:
    c = cer(expected, heard)
    reps = find_repetitions(expected, heard)
    omits = find_omissions(expected, heard)
    exp_len = len(normalize(expected, True).replace(" ", ""))
    heard_len = len(normalize(heard, True).replace(" ", ""))
    ratio = heard_len / exp_len if exp_len else 1.0
    issues = []
    if not heard.strip():
        issues.append("Back-transcription is empty: the audio may be silent or unintelligible.")
    if c > cer_threshold:
        issues.append(f"Character error rate {c:.2f} is above the {cer_threshold:.2f} threshold.")
    if reps:
        issues.append(f"Repeated speech detected: {reps}")
    if omits:
        issues.append(f"Script text missing from the audio: {omits}")
    if ratio < 0.7:
        issues.append(f"Audio carries only {ratio:.0%} of the script's characters (truncation?).")
    elif ratio > 1.4:
        issues.append(f"Audio carries {ratio:.0%} of the script's characters (extra content?).")
    return {"ok": not issues, "cer": round(c, 3), "length_ratio": round(ratio, 3), "repetitions": reps,
            "omissions": omits, "issues": issues}


# ---------------------------------------------------------------- translation checks
_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_MATH = re.compile(r"\$\$.+?\$\$|\$[^$\n]+?\$", re.S)
_TEXTCMD = re.compile(r"\\text\{[^{}]*\}")


def _ascii_digits(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    return "".join(str(unicodedata.decimal(ch)) if unicodedata.category(ch) == "Nd" else ch for ch in t)


def numbers(text: str) -> Counter:
    """Numbers as ASCII strings, so 25, २५ and ௨௫ all count as the same number."""
    t = _ascii_digits(_strip_html(text))
    return Counter(n.replace(",", "") for n in _NUM.findall(t))


def _strip_html(t: str) -> str:
    return re.sub(r"<[^>]+>", " ", t)


def structure(text: str) -> dict[str, int]:
    return {
        "table_rows": len(re.findall(r"^\s*\|.*\|\s*$", text, re.M)) + len(re.findall(r"<tr\b", text, re.I)),
        "headings": len(re.findall(r"^\s{0,3}#{1,6}\s", text, re.M)),
        "list_items": len(re.findall(r"^\s*(?:[-*+]|\d+[.)])\s+\S", text, re.M)),
        "math_segments": len(_MATH.findall(text)),
        "code_fences": text.count("```") // 2,
    }


def math_segments(text: str) -> list[str]:
    return [re.sub(r"\s+", "", _TEXTCMD.sub("", m)) for m in _MATH.findall(text)]


def check_translation(source: str, translation: str, target_lang: str, truncated: bool = False) -> dict:
    issues: list[str] = []
    warnings: list[str] = []
    metrics: dict = {}
    src, tr = source.strip(), translation.strip()
    if not tr:
        return {"ok": False, "issues": ["Translation is empty."], "warnings": [], "metrics": {}}
    if truncated:
        issues.append("The API reported the translation as truncated (incomplete).")
    if normalize(src) == normalize(tr) and target_lang != "en" and script_counts(src):
        issues.append("Translation is identical to the source (nothing was translated).")

    # 1. script
    body = _MATH.sub(" ", re.sub(r"<[^>]+>|https?://\S+", " ", tr))
    counts = script_counts(body)
    total = sum(counts.values())
    want = LANG_SCRIPT.get(target_lang)
    if want and total >= 20:
        share = counts.get(want, 0) / total
        metrics["target_script_share"] = round(share, 3)
        if share < 0.35:
            issues.append(f"Only {share:.0%} of letters are in the expected {want} script (wrong language/untranslated?).")
        elif share < 0.6:
            warnings.append(f"{share:.0%} of letters are in {want} script (code-mixed or partly untranslated).")

    # 2. numbers
    sn, tn = numbers(src), numbers(tr)
    missing = sn - tn
    if sn:
        frac = sum(missing.values()) / sum(sn.values())
        metrics["numbers_missing_fraction"] = round(frac, 3)
        if frac > 0.2:
            issues.append(f"Numbers from the source are missing in the translation: {sorted(missing)[:10]}")
        elif frac > 0:
            warnings.append(f"A few source numbers were not found verbatim: {sorted(missing)[:10]}")

    # 3. structure
    ss, ts = structure(src), structure(tr)
    metrics["structure_source"], metrics["structure_translation"] = ss, ts
    for k in ss:
        if ss[k] != ts[k]:
            issues.append(f"Structure changed: {k} {ss[k]} -> {ts[k]}.")
    sm, tm = math_segments(src), math_segments(tr)
    if sm != tm:
        warnings.append("LaTeX segments differ from the source (ignoring \\text{...}).")

    # 4. length / omission
    ratio = len(tr) / max(1, len(src))
    metrics["length_ratio"] = round(ratio, 3)
    if len(src) > 80 and ratio < 0.4:
        issues.append(f"Translation is only {ratio:.0%} as long as the source (possible omission).")
    s_sent = len(re.findall(r"[.!?।॥]", src))
    t_sent = len(re.findall(r"[.!?।॥]", tr))
    if s_sent >= 4 and t_sent < 0.6 * s_sent:
        issues.append(f"Sentence count dropped from {s_sent} to {t_sent} (possible omission).")
    return {"ok": not issues, "issues": issues, "warnings": warnings, "metrics": metrics}


def english_coverage(source_en: str, back_translation_en: str) -> float:
    """Share of the source's content words that survive a round trip. Crude, but cheap and reference-free."""
    words = {w for w in re.findall(r"[a-z]{4,}", normalize(source_en))}
    if not words:
        return 1.0
    back = set(re.findall(r"[a-z]{4,}", normalize(back_translation_en)))
    return len(words & back) / len(words)
