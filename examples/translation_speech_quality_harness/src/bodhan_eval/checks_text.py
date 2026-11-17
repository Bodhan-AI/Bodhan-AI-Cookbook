"""Tier 1: checks that need no model call. Each returns candidates; none of them flags anything by itself."""

import math
import re
from collections import Counter
from decimal import Decimal, InvalidOperation

from . import config
from .normalize import ascii_digits, mask_protected, normalize, protected_spans, script_of_char

MIN_ITEMS_FOR_STATS = 8  # below this, a per-language median is not meaningful
OUTLIER_Z = 3.0  # robust z-score beyond which a value is an outlier (about 3 standard deviations)


def cand(check, category, cls, strength, source_span=None, translation_span=None, severity=None, **evidence):
    """A suspicion from one check. cls is 'exact' (can flag alone), 'noisy' (needs support) or 'weak' (supporting only)."""
    return {"check": check, "category": category, "class": cls, "strength": round(strength, 3),
            "source_span": list(source_span) if source_span else None,
            "translation_span": list(translation_span) if translation_span else None,
            "severity_hint": severity, "evidence": evidence}


# ---------------------------------------------------------------- numbers

_NUM = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)*")
_UNIT = re.compile(r"[ \t]?(%|°[ \t]?[CF]|°|km|kg|mg|cm|mm|ml|g|m|l|L|s|ms|h|min)(?![^\W\d_])")
_CURRENCY = re.compile(r"(₹|Rs\.?|\$|€|£)[ \t]?$")
_SCALES = {Decimal(10) ** k for k in (2, 3, 5, 7)}  # hundred, thousand, lakh, crore: 1 million == 10 lakh
_WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
                                         "sixteen seventeen eighteen nineteen".split())}
_WORDS.update({w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())})
_SCALE_WORDS = {"hundred": 100, "thousand": 1000, "lakh": 10**5, "lakhs": 10**5, "million": 10**6, "crore": 10**7, "crores": 10**7, "billion": 10**9}
# Scale words written after digits ("91 கோடி", "৬৫ লক্ষ"), matched as word prefixes so inflected forms count.
_INDIC_SCALES = [
    (10**3, "हज़ार हजार হাজার ஆயிரம் ஆயிரத் હજાર ਹਜ਼ਾਰ ਹਜਾਰ ହଜାର ಸಾವಿರ ആയിരം వేల వేయి"),
    (10**5, "लाख লক্ষ লাখ லட்சம் லட்சத் લાખ ਲੱਖ ଲକ୍ଷ ಲಕ್ಷ ലക്ഷം లక్ష"),
    (10**6, "मिलियन মিলিয়ন மில்லியன் મિલિયન ਮਿਲੀਅਨ ମିଲିୟନ ಮಿಲಿಯನ್ മില്യൺ మిలియన్"),
    (10**7, "करोड़ करोड कोटी কোটি கோடி કરોડ ਕਰੋੜ କୋଟି ಕೋಟಿ കോടി కోటి కోట్ల"),
    (10**9, "अरब अब्ज বিলিয়ন பில்லியன் અબજ ਅਰਬ ବିଲିୟନ ಬಿಲಿಯನ್ ബില്യൺ బిలియన్"),
]
_SCALE_AFTER = re.compile(r"[ \t]*([^\W\d_][\wऀ-෿]*)")  # Indic vowel signs are not \w


def _value(tok: str):
    if "," in tok and "." in tok:
        tok = tok.replace(",", "") if tok.rfind(".") > tok.rfind(",") else tok.replace(".", "").replace(",", ".")
    elif "," in tok:
        parts = tok.split(",")
        grouped = all(len(p) == 3 for p in parts[1:]) or (len(parts[-1]) == 3 and all(len(p) == 2 for p in parts[1:-1]))
        tok = tok.replace(",", "") if grouped or len(parts) > 2 else tok.replace(",", ".")
    elif tok.count(".") > 1:
        return tok  # a version or dotted date: compare as text
    try:
        return Decimal(tok).normalize()
    except InvalidOperation:
        return tok


def numbers_in(text: str) -> list[dict]:
    """Digit numbers outside protected spans, any script. Spans are in the original text."""
    t = mask_protected(ascii_digits(text))
    out = []
    for m in _NUM.finditer(t):
        unit = _UNIT.match(t, m.end())
        cur = _CURRENCY.search(t, max(0, m.start() - 4), m.start())
        value = _value(m.group())
        if isinstance(value, Decimal) and not unit:
            value *= _scale_after(t, m.end())
        out.append({"value": value.normalize() if isinstance(value, Decimal) else value, "token": text[m.start():m.end()], "span": (m.start(), m.end()),
                    "unit": (unit.group(1).replace(" ", "") if unit else None), "currency": (cur.group(1).rstrip(".") if cur else None)})
    return out


def _scale_after(text: str, pos: int) -> int:
    """The multiplier of a scale word right after a number ("6.5 million", "65 লক্ষ"), else 1."""
    m = _SCALE_AFTER.match(text, pos)
    if not m:
        return 1
    w = m.group(1)
    if w.lower() in _SCALE_WORDS and w.lower() != "hundred":
        return _SCALE_WORDS[w.lower()]
    return next((v for v, words in _INDIC_SCALES if any(w.startswith(x) for x in words.split())), 1)


def word_numbers(text: str) -> list[Decimal]:
    """Values of English number words ('three', 'one million', 'twenty-five')."""
    values, total, current, last = [], 0, 0, None  # last: None, "small" (<20), "tens", "scale", "and"

    def flush():
        nonlocal total, current, last
        if last is not None:
            values.append(Decimal(total + current))
        total, current, last = 0, 0, None

    for w in re.findall(r"[a-z]+", text.lower().replace("-", " ")):
        if w in _WORDS:
            v = _WORDS[w]
            # "five one" or "twenty thirty" cannot combine: they are two numbers.
            if last == "small" or (last == "tens" and (v >= 10)):
                flush()
            current += v
            last = "tens" if v >= 20 else "small"
        elif w in _SCALE_WORDS and last in ("small", "tens"):
            if _SCALE_WORDS[w] == 100:
                current *= 100
            else:
                total += current * _SCALE_WORDS[w]
                current = 0
            last = "scale"
        elif w == "and" and last == "scale":
            last = "and"  # "one hundred and five"
        else:
            if last == "and":
                last = "scale"
            flush()
    flush()
    return values


def _equivalent(a, b) -> bool:
    if a == b:
        return True
    if isinstance(a, Decimal) and isinstance(b, Decimal) and a and b:
        return a / b in _SCALES or b / a in _SCALES
    return False


def check_numbers(item) -> list[dict]:
    src, tr = numbers_in(item["source"]), numbers_in(item["translation"])
    src_words = word_numbers(item["source"])
    used, unmatched_src, cands = set(), [], []
    for pass_exact in (True, False):  # exact matches first, so scale matching cannot steal them
        for s in src:
            if s.get("matched"):
                continue
            for j, t in enumerate(tr):
                if j not in used and (s["value"] == t["value"] if pass_exact else _equivalent(s["value"], t["value"])):
                    used.add(j)
                    s["matched"] = j
                    break
    for s in src:
        if "matched" not in s:
            unmatched_src.append(s)
            continue
        t = tr[s["matched"]]
        for kind in ("unit", "currency"):
            # A bare ° is compatible with °C/°F: the unit name is often spelled out ("২৫° সেলসিয়াস").
            if s[kind] and t[kind] and s[kind] != t[kind] and not ({s[kind], t[kind]} & {"°"} and s[kind][0] == t[kind][0] == "°"):
                cands.append(cand("numbers", "number", "exact", 0.9, s["span"], t["span"], "major",
                                  detail=f"{kind} changed", source=f"{s['token']} {s[kind]}", translation=f"{t['token']} {t[kind]}"))
    unmatched_tr = [t for j, t in enumerate(tr) if j not in used and not any(_equivalent(w, t["value"]) for w in src_words)]
    # Pair leftovers in reading order: a source number replaced by a different number in the translation.
    for s, t in zip(unmatched_src, unmatched_tr, strict=False):
        cands.append(cand("numbers", "number", "exact", 0.9, s["span"], t["span"], "major",
                          detail="number changed", source=s["token"], translation=t["token"]))
    for s in unmatched_src[len(unmatched_tr):]:
        cands.append(cand("numbers", "number", "noisy", 0.4, s["span"], None, "major",
                          detail="source number not found as digits in the translation (may be written as a word)", source=s["token"]))
    for t in unmatched_tr[len(unmatched_src):]:
        cands.append(cand("numbers", "number", "noisy", 0.5, None, t["span"], "major",
                          detail="number in the translation with no counterpart in the source", translation=t["token"]))
    return cands


# ---------------------------------------------------------------- format

_HEADING = re.compile(r"^(#{1,6})[ \t]+\S", re.M)
_LIST = re.compile(r"^[ \t]*([-*+]|\d+[.)])[ \t]+\S", re.M)
_TABLE_LINE = re.compile(r"^[ \t]*\|.*$", re.M)
_TABLE_SEP = re.compile(r"^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(\|[ \t]*:?-{3,}:?[ \t]*)*\|?[ \t]*$")
_MATH_TEXT = re.compile(r"\\(text|mathrm|textbf|textit|operatorname|mbox)\{[^{}]*\}")
_HTML_NAME = re.compile(r"</?([a-zA-Z][a-zA-Z0-9]*)")


def _tables(masked: str):
    tables, cur = [], []
    for m in _TABLE_LINE.finditer(masked):
        if cur and masked[cur[-1].end():m.start()].strip("\n") == "" and masked.count("\n", cur[-1].end(), m.start()) == 1:
            cur.append(m)
        else:
            if cur:
                tables.append(cur)
            cur = [m]
    if cur:
        tables.append(cur)
    out = []
    for rows in tables:
        cols = tuple(len(r.group().strip().strip("|").split("|")) for r in rows if not _TABLE_SEP.match(r.group()))
        has_sep = any(_TABLE_SEP.match(r.group()) for r in rows)
        out.append(((len(rows), cols, has_sep), (rows[0].start(), rows[-1].end())))
    return out


def structure(text: str) -> dict:
    """Markdown/LaTeX elements: sequences for structure, multisets for protected content that may move with word order."""
    digits = ascii_digits(text)
    prot = protected_spans(digits)
    masked = mask_protected(digits, prot)
    seq = {
        "heading": [(len(m.group(1)), (m.start(), m.end())) for m in _HEADING.finditer(masked)],
        "list": [("number" if m.group(1)[0].isdigit() else "bullet", (m.start(), m.end())) for m in _LIST.finditer(masked)],
        "table": _tables(masked),
        "html": [(_HTML_NAME.match(text[s:e]).group(1).lower(), (s, e)) for s, e, k in prot if k == "html"],
    }
    bag = {"math": [], "code": [], "url": [], "fence": []}
    for s, e, k in prot:
        if k in ("env", "dmath", "imath"):
            bag["math"].append((re.sub(r"\s+", "", _MATH_TEXT.sub(r"\\\1{}", text[s:e])), (s, e)))
        elif k in bag:
            bag[k].append((text[s:e].strip(), (s, e)))
    bold = [(m.start(), m.end()) for m in re.finditer(r"\*\*", masked)]
    return {"seq": seq, "bag": bag, "bold": bold}


_FORMAT_SEVERITY = {"table": "major", "math": "major", "fence": "major", "heading": "minor", "list": "minor", "code": "minor",
                    "url": "minor", "html": "minor"}


def check_format(item) -> list[dict]:
    a, b = structure(item["source"]), structure(item["translation"])
    end_t = (len(item["translation"]), len(item["translation"]))
    cands = []
    for kind, sa in a["seq"].items():
        sb = b["seq"][kind]
        if [v for v, _ in sa] == [v for v, _ in sb]:
            continue
        i = next((i for i, (x, y) in enumerate(zip(sa, sb, strict=False)) if x[0] != y[0]), min(len(sa), len(sb)))
        cands.append(cand("format", "format", "exact", 0.95, sa[i][1] if i < len(sa) else None, sb[i][1] if i < len(sb) else end_t,
                          _FORMAT_SEVERITY[kind], detail=f"{kind} structure differs", source=[v for v, _ in sa], translation=[v for v, _ in sb]))
    for kind, ba in a["bag"].items():
        bb = b["bag"][kind]
        missing = Counter(v for v, _ in ba) - Counter(v for v, _ in bb)
        extra = Counter(v for v, _ in bb) - Counter(v for v, _ in ba)
        if not missing and not extra:
            continue
        s_span = next((sp for v, sp in ba if v in missing), None)
        t_span = next((sp for v, sp in bb if v in extra), None)
        cands.append(cand("format", "format", "exact", 0.95, s_span, t_span or end_t, _FORMAT_SEVERITY[kind],
                          detail=f"{kind} content differs", missing=sorted(missing), extra=sorted(extra)))
    if len(a["bold"]) % 2 == 0 and len(b["bold"]) % 2 == 1:
        cands.append(cand("format", "format", "exact", 0.9, None, b["bold"][-1], "minor", detail="unbalanced ** emphasis marker"))
    return cands


# ---------------------------------------------------------------- script / untranslated

_LATIN_RUN = re.compile(r"[A-Za-z][A-Za-z'’-]*(?:[\s,;:()\"'.\d%-]+[A-Za-z][A-Za-z'’-]*)*")
_WORD = re.compile(r"\S+")


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalize(text)[0])


def _contains(haystack: list[str], needle: list[str]) -> bool:
    return f" {' '.join(needle)} " in f" {' '.join(haystack)} "


def check_script(item) -> list[dict]:
    expected = config.script_of(item["lang"])
    t = mask_protected(item["translation"])
    cands = []

    # Words written in a non-Latin script other than the expected one.
    words = []
    for m in _WORD.finditer(t):
        scripts = Counter(s for ch in m.group() if (s := script_of_char(ch)))
        if scripts:
            words.append((scripts.most_common(1)[0][0], sum(scripts.values()), (m.start(), m.end())))
    total = sum(n for _, n, _ in words) or 1
    foreign = [w for w in words if w[0] not in (expected, "LATIN")]
    latin_letters = sum(n for s, n, _ in words if s == "LATIN")
    if sum(n for _, n, _ in foreign) / total >= 0.2:
        cands.append(cand("script", "wrong_language", "exact", 0.95, None, (0, len(item["translation"])), "major",
                          detail=f"text is largely in another script (expected {expected})",
                          scripts=dict(Counter(s for s, _, _ in foreign).most_common())))
    else:
        run = []
        for w in words + [(None, 0, None)]:  # sentinel flushes the last run
            if run and (w[0] != run[-1][0] or w[0] in (expected, "LATIN", None)):
                if len(run) >= 2:
                    cands.append(cand("script", "wrong_language", "exact", 0.85, None, (run[0][2][0], run[-1][2][1]), "major",
                                      detail=f"{len(run)} consecutive words in {run[0][0]} script", script=run[0][0]))
                run = []
            if w[0] not in (expected, "LATIN", None):
                run.append(w)

    # English left untranslated: a run of Latin words that also appears word-for-word in the source.
    src_words = _words(mask_protected(item["source"]))
    flagged_latin = False
    for m in _LATIN_RUN.finditer(t):
        rw = _words(m.group())
        alpha = [w for w in rw if not w.isdigit()]
        # A title-case run ("Tierra del Sol Gallery") is a name kept in Latin script, which is normal; prose has lowercase words.
        name = all(w[0].isupper() for w in re.findall(r"[A-Za-z]+", m.group()) if len(w) > 3)
        if len(alpha) >= 4 and not name and _contains(src_words, rw):
            flagged_latin = True
            cands.append(cand("script", "wrong_language", "exact", 0.9, None, (m.start(), m.end()), "major",
                              detail="English text from the source left untranslated", text=m.group()[:200]))
    if not flagged_latin and latin_letters / total >= 0.6 and len(_LATIN_RUN.findall(t)) and expected != "LATIN":
        cands.append(cand("script", "wrong_language", "noisy", 0.6, None, (0, len(item["translation"])), "major",
                          detail="translation is mostly Latin script"))
    return cands


# ---------------------------------------------------------------- statistics shared by run-level checks

def robust_z(values: list[float]) -> list[float]:
    """Distance from the median in units of robust spread (median absolute deviation)."""
    if not values:
        return []
    med = sorted(values)[len(values) // 2] if len(values) % 2 else sum(sorted(values)[len(values) // 2 - 1:len(values) // 2 + 1]) / 2
    dev = sorted(abs(v - med) for v in values)
    mad = dev[len(dev) // 2] if len(dev) % 2 else (dev[len(dev) // 2 - 1] + dev[len(dev) // 2]) / 2
    scale = 1.4826 * mad or (sum(dev) / len(dev)) * 1.2533  # fall back to mean absolute deviation when MAD is 0
    return [0.0 if not scale else (v - med) / scale for v in values]


def length_ratio(item) -> float | None:
    def size(text):
        return len(re.sub(r"\s", "", mask_protected(text, fill="")))
    s, t = size(item["source"]), size(item["translation"])
    return math.log(t / s) if s and t else None


def length_candidates(items: list[dict]) -> tuple[dict[int, list[dict]], set[str]]:
    """Per-language outliers of the translation/source length ratio. Weak evidence: supports, never flags.
    Returns candidates by item index and the languages that had enough items to compute statistics."""
    out, by_lang = {}, {}
    for i, it in enumerate(items):
        r = length_ratio(it)
        if r is not None:
            by_lang.setdefault(it["lang"], []).append((i, r))
    for _lang, rows in by_lang.items():
        if len(rows) < MIN_ITEMS_FOR_STATS:
            continue
        for (i, _), z in zip(rows, robust_z([r for _, r in rows]), strict=True):
            if z <= -OUTLIER_Z:
                out[i] = [cand("length", "omission", "weak", min(1.0, -z / 6), detail="translation much shorter than usual", z=round(z, 2))]
            elif z >= OUTLIER_Z:
                out[i] = [cand("length", "addition", "weak", min(1.0, z / 6), detail="translation much longer than usual", z=round(z, 2))]
    return out, {lang for lang, rows in by_lang.items() if len(rows) >= MIN_ITEMS_FOR_STATS}


def chrf(hypothesis: str, reference: str) -> float:
    """chrF++ (character n-grams plus word pairs) on normalized text, 0-100."""
    from sacrebleu.metrics import CHRF

    return round(CHRF(word_order=2).sentence_score(normalize(hypothesis)[0], [normalize(reference)[0]]).score, 2)


def reference_scores(items: list[dict]) -> tuple[dict[int, float], dict[int, list[dict]]]:
    """chrF++ against the reference where one exists; low outliers within a language become noisy candidates."""
    scores, by_lang, cands = {}, {}, {}
    for i, it in enumerate(items):
        if it.get("reference") and it.get("translation"):
            scores[i] = chrf(it["translation"], it["reference"])
            by_lang.setdefault(it["lang"], []).append(i)
    for _lang, idx in by_lang.items():
        if len(idx) < MIN_ITEMS_FOR_STATS:
            continue
        for i, z in zip(idx, robust_z([scores[i] for i in idx]), strict=False):
            if z <= -OUTLIER_Z:
                cands[i] = [cand("reference", "mistranslation", "noisy", min(1.0, -z / 6),
                                 detail="far below other items' chrF++ against their references", chrf=scores[i], z=round(z, 2))]
    return scores, cands


TEXT_CHECKS = {"numbers": check_numbers, "format": check_format, "script": check_script}
