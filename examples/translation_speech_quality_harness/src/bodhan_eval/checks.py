"""checks.base: Flag schema, per-item check context and the check registry.
checks.numbers: Number / unit / date preservation (deterministic, zero cost).
checks.script: Wrong language / script and untranslated text (deterministic, zero cost).
checks.format: Markdown / table / LaTeX / code integrity (deterministic, zero cost).
checks.length: Weak text signals and free metadata signals.
checks.audio: Acoustic sanity checks on the speech audio (deterministic, zero model cost).
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from .audio import AudioProfile
from .config import CATEGORIES, LANGUAGES, SEVERITIES
from .infra import RunStats
from .text import NormText, Region, expected_script, find_protected, foreign_runs, mask_protected, normalize, normalize_with_map, script_profile, sentences

# -------------------- base
log = logging.getLogger("bodhan_eval.checks")


@dataclass
class Flag:
    check_id: str
    category: str
    severity: str
    span: tuple[float, float] | None  # chars into the translation, or seconds into the audio
    unit: str  # "chars" | "seconds"
    message: str  # one plain-English line: what is wrong and where
    evidence: dict = field(default_factory=dict)
    confidence: float = 0.9
    # True = weak signal: never reported alone, only if another independent signal agrees (fusion)
    needs_corroboration: bool = False

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown category {self.category!r}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity {self.severity!r}")
        if self.unit not in ("chars", "seconds"):
            raise ValueError(f"unknown unit {self.unit!r}")

    def to_dict(self) -> dict:
        span = None
        if self.span is not None:
            span = [int(self.span[0]), int(self.span[1])] if self.unit == "chars" else [round(self.span[0], 3), round(self.span[1], 3)]
        return {
            "check_id": self.check_id,
            "category": self.category,
            "severity": self.severity,
            "span": span,
            "unit": self.unit,
            "message": self.message,
            "evidence": self.evidence,
            "confidence": round(self.confidence, 3),
            "needs_corroboration": self.needs_corroboration,
        }


@dataclass
class CheckContext:
    item_id: str
    lang: str
    source: str
    translation: str | None = None
    reference: str | None = None
    translation_meta: dict = field(default_factory=dict)  # e.g. {"truncated": True} when we generated it
    audio: AudioProfile | None = None
    audio_text: str | None = None  # the text the audio should say (normally the translation)
    stats: RunStats = field(default_factory=RunStats)
    thresholds: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)  # checks record numeric evidence here
    # ---- round-trip inputs (tier 2), filled by the pipeline -----------------------
    back_translation: str | None = None  # Indic-Translate output, target "en"
    bt_truncated: bool = False  # the back-translation call itself was cut off
    asr_text: str | None = None  # Indic-Transcribe output for the supplied audio
    bt: Any = None  # roundtrip.compare.BTResult
    asr: Any = None  # roundtrip.compare.ASRResult
    floors: dict = field(default_factory=dict)  # signal -> noise floor value for this item's language
    prior_flags: list = field(default_factory=list)  # Tier-1 flags of this item (corroboration)

    # ---- derived views, computed once per item ----------------------------------
    @cached_property
    def src_regions(self) -> list[Region]:
        return find_protected(self.source)

    @cached_property
    def tgt_regions(self) -> list[Region]:
        return find_protected(self.translation or "")

    @cached_property
    def tgt_norm(self) -> NormText:
        return normalize_with_map(self.translation or "")

    @cached_property
    def src_sentences(self) -> list[tuple[int, int]]:
        return sentences(self.source, self.src_regions)

    @cached_property
    def tgt_sentences(self) -> list[tuple[int, int]]:
        return sentences(self.translation or "", self.tgt_regions)

    def aligned_tgt_sentence(self, src_pos: int) -> tuple[int, int] | None:
        """Target sentence at the same index as the source sentence containing ``src_pos``.
        Only when both sides have the same number of sentences (else None: no guessing)."""
        if len(self.src_sentences) != len(self.tgt_sentences):
            return None
        for i, (s, e) in enumerate(self.src_sentences):
            if s <= src_pos < e:
                return self.tgt_sentences[i]
        return None

    def th(self, name: str) -> float:
        return float(self.thresholds[name])


CheckFn = Callable[[CheckContext], list[Flag]]


@dataclass(frozen=True)
class CheckSpec:
    check_id: str
    fn: CheckFn
    needs: tuple[str, ...]  # context fields that must be present: "translation", "audio", "reference"
    tier: int
    description: str


REGISTRY: dict[str, CheckSpec] = {}


def register(check_id: str, *, needs: tuple[str, ...], tier: int = 1, description: str = ""):
    def deco(fn: CheckFn) -> CheckFn:
        if check_id in REGISTRY:
            raise ValueError(f"duplicate check id {check_id!r}")
        REGISTRY[check_id] = CheckSpec(check_id, fn, needs, tier, description or (fn.__doc__ or "").strip().split("\n")[0])
        return fn

    return deco


def _missing(ctx: CheckContext, needs: tuple[str, ...]) -> str | None:
    for need in needs:
        if getattr(ctx, need, None) in (None, ""):
            return need
    return None


def run_checks(ctx: CheckContext, *, tier: int = 1, only: set[str] | None = None) -> tuple[list[Flag], list[dict]]:
    """Run every registered check of ``tier``. Returns (flags, per-check records)."""
    flags: list[Flag] = []
    records: list[dict] = []
    for check_id in sorted(REGISTRY):
        spec = REGISTRY[check_id]
        if spec.tier != tier or (only is not None and check_id not in only):
            continue
        missing = _missing(ctx, spec.needs)
        if missing:
            records.append({"check": check_id, "status": "skipped", "reason": f"no {missing}"})
            continue
        try:
            out = spec.fn(ctx)
        except Exception as exc:  # a broken check never stops the item
            log.exception("check_failed", extra={"fields": {"item": ctx.item_id, "check": check_id, "error": repr(exc)}})
            records.append({"check": check_id, "status": "error", "reason": f"{type(exc).__name__}: {exc}"})
            continue
        for f in out:
            log.debug("flag", extra={"fields": {"item": ctx.item_id, "check": check_id, "category": f.category, "span": f.span, "message": f.message}})
        records.append({"check": check_id, "status": "ok", "flags": len(out)})
        flags.extend(out)
    return flags, records


# -------------------- numbers
_NUM = re.compile(r"\d+(?:[.,]\d+)*")


_WESTERN = re.compile(r"^\d{1,3}(?:,\d{3})+$")


_INDIAN = re.compile(r"^\d{1,2}(?:,\d{2})*,\d{3}$")


_UNITS = sorted(
    ["km/h", "m/s", "km/s", "°C", "°F", "°", "km", "cm", "mm", "nm", "µm", "kg", "mg", "ml", "mL", "kW", "MW", "kWh", "kHz", "MHz", "GHz", "Hz",
     "GB", "MB", "KB", "TB", "kb", "mph", "ft", "mi", "lb", "oz", "m", "g", "l", "L", "s", "ms", "h", "min", "K", "V", "A", "W", "N", "J", "Pa",
     "%", "₹", "$", "€", "£"],
    key=len,
    reverse=True,
)


_UNIT_AFTER = re.compile(r"\s?(" + "|".join(re.escape(u) for u in _UNITS) + r")(?![A-Za-z])")


_CURRENCY_BEFORE = re.compile(r"([₹$€£])\s?$")


_UNIT_WORDS = {"percent": "%", "per cent": "%", "degrees celsius": "°C", "degree celsius": "°C", "celsius": "°C", "fahrenheit": "°F",
               "rupees": "₹", "rupee": "₹", "dollars": "$", "dollar": "$", "kilometres": "km", "kilometers": "km", "kilograms": "kg"}


_ONES = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
                                    "sixteen seventeen eighteen nineteen".split())}


_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}


_SCALES = {"hundred": 100, "thousand": 1000, "lakh": 100_000, "million": 1_000_000, "crore": 10_000_000, "billion": 1_000_000_000}


_ORDINALS = {w: i + 1 for i, w in enumerate("first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth".split())}


_WORD = re.compile(r"[a-z]+(?:-[a-z]+)?")


@dataclass(frozen=True)
class Num:
    value: str
    start: int  # offsets in the ORIGINAL text
    end: int
    unit: str | None
    unit_end: int  # end offset including the unit symbol (== end when no unit)


def _canonical(tok: str) -> list[str]:
    """Canonical value strings for one numeric token."""
    if "," in tok:
        int_part = tok.split(".")[0]
        if _WESTERN.match(int_part) or _INDIAN.match(int_part):
            tok = tok.replace(",", "")
        else:
            return [v for part in tok.split(",") for v in _canonical(part)]
    if tok.count(".") > 1:  # version numbers / dates like 1.2.3 stay literal
        return [tok]
    if "." in tok:
        whole, frac = tok.split(".")
        frac = frac.rstrip("0")
        whole = whole.lstrip("0") or "0"
        return [f"{whole}.{frac}" if frac else whole]
    return [tok.lstrip("0") or "0"]


def extract_numbers(text: str) -> list[Num]:
    masked = mask_protected(text)
    nt = normalize_with_map(masked, casefold_latin=False)
    out: list[Num] = []
    for m in _NUM.finditer(nt.norm):
        span = nt.to_orig(m.start(), m.end())
        if span is None:
            continue
        unit, unit_end = None, span[1]
        um = _UNIT_AFTER.match(nt.norm, m.end())
        if um:
            unit = um.group(1)
            unit_end = (nt.to_orig(m.start(), um.end()) or span)[1]
        else:
            cm = _CURRENCY_BEFORE.search(nt.norm[max(0, m.start() - 2) : m.start()])
            unit = cm.group(1) if cm else None
        for v in _canonical(m.group()):
            out.append(Num(v, span[0], span[1], unit, unit_end))
    return out


def english_number_words(text: str) -> list[str]:
    """Values of number words in English text ("twenty-five" → "25", "two hundred" → "200")."""
    words = [w for tok in _WORD.findall(text.lower()) for w in tok.split("-")]
    values: list[str] = []
    total = current = 0
    active = False
    for w in words + ["<end>"]:
        if w in _ONES or w in _TENS:
            current += _ONES.get(w, 0) + _TENS.get(w, 0)
            active = True
        elif w in _SCALES and active:
            current = max(current, 1) * _SCALES[w]
            if _SCALES[w] >= 1000:
                total, current = total + current, 0
        elif w in _SCALES:  # "a hundred"
            current, active = _SCALES[w], True
        else:
            if active:
                values.append(str(total + current))
            total = current = 0
            active = False
            if w in _ORDINALS:
                values.append(str(_ORDINALS[w]))
    return values


def _source_units(text: str) -> set[str]:
    units = {n.unit for n in extract_numbers(text) if n.unit}
    low = text.lower()
    units |= {sym for word, sym in _UNIT_WORDS.items() if word in low}
    return units


@register("numbers", needs=("translation",), description="Numbers, dates and unit symbols match the source")
def check_numbers(ctx: CheckContext) -> list[Flag]:
    src = extract_numbers(ctx.source)
    tgt = extract_numbers(ctx.translation)
    src_count = Counter(n.value for n in src)
    tgt_count = Counter(n.value for n in tgt)
    missing = src_count - tgt_count
    extra = tgt_count - src_count
    # number words in the source may legitimately become digits
    words = Counter(english_number_words(mask_protected(ctx.source)))
    for v in list(extra):
        k = min(extra[v], words.get(v, 0))
        if k:
            extra[v] -= k
            words[v] -= k
    extra += Counter()  # drop zeros
    ctx.metrics["numbers"] = {"source": sorted(src_count.elements()), "translation": sorted(tgt_count.elements())}

    flags: list[Flag] = []
    missing_list = sorted(missing.elements(), key=lambda v: next(n.start for n in src if n.value == v))
    extra_nums = []
    used: Counter = Counter()
    for n in tgt:  # pick the actual extra occurrences (last ones of each value)
        if used[n.value] < tgt_count[n.value] - extra.get(n.value, 0):
            used[n.value] += 1
        elif extra.get(n.value, 0):
            extra_nums.append(n)

    # pair missing with extra in order of appearance = "changed" numbers (strong)
    pairs = list(zip(missing_list, extra_nums, strict=False))
    for want, got in pairs:
        flags.append(Flag(
            "numbers", "number_unit", "major", (got.start, got.end), "chars",
            f"Number changed: source has {want}, translation has {got.value} at chars {got.start}-{got.end}.",
            {"source_value": want, "translation_value": got.value, "translation_text": ctx.translation[got.start:got.end]},
            confidence=0.9,
        ))
    for want in missing_list[len(pairs):]:
        src_num = next(n for n in src if n.value == want)
        span = ctx.aligned_tgt_sentence(src_num.start)
        flags.append(Flag(
            "numbers", "number_unit", "major", span, "chars",
            f"Source number {want} does not appear as digits in the translation" + (" (span = aligned sentence)." if span else "."),
            {"source_value": want, "source_span": [src_num.start, src_num.end], "note": "may be spelled out in words; needs corroboration"},
            confidence=0.5, needs_corroboration=True,
        ))
    for got in extra_nums[len(pairs):]:
        flags.append(Flag(
            "numbers", "number_unit", "major", (got.start, got.end), "chars",
            f"Translation has number {got.value} that is not in the source.",
            {"translation_value": got.value},
            confidence=0.5, needs_corroboration=True,
        ))

    # unit symbols next to numbers in the translation must exist in the source
    src_units = _source_units(ctx.source)
    for n in tgt:
        if n.unit and n.unit not in src_units and not (n.unit == "°" and ({"°C", "°F"} & src_units)):
            flags.append(Flag(
                "numbers", "number_unit", "major", (n.start, n.unit_end), "chars",
                f"Unit '{n.unit}' after {n.value} in the translation is not used in the source (source units: {sorted(src_units) or 'none'}).",
                {"translation_unit": n.unit, "source_units": sorted(src_units)},
                confidence=0.8,
            ))
    return flags


# -------------------- script
@register("script", needs=("translation",), description="Output is in the expected language script, nothing left untranslated")
def check_script(ctx: CheckContext) -> list[Flag]:
    t = ctx.translation
    whole = (0, len(t))
    if not t.strip():
        return [Flag("script", "omission", "critical", (0, 0), "chars", "Translation is empty.", {}, confidence=0.99)]

    exp = expected_script(ctx.lang)
    lang_name = LANGUAGES[ctx.lang].name
    if exp != "Latin" and normalize(t) == normalize(ctx.source):
        return [Flag("script", "wrong_language_script", "critical", whole, "chars",
                     f"Translation is identical to the English source (untranslated; expected {lang_name}).",
                     {"expected_script": exp}, confidence=0.99)]

    prof = script_profile(t, ctx.tgt_regions)
    ratio = prof.ratio(exp)
    ctx.metrics["script"] = {"expected": exp, "ratio": round(ratio, 4), "counts": prof.counts}
    if prof.letters >= ctx.th("script_min_letters") and ratio < ctx.th("script_ratio_min"):
        what = "English (untranslated)" if prof.dominant == "Latin" else f"{prof.dominant} script"
        return [Flag("script", "wrong_language_script", "critical", whole, "chars",
                     f"Output is mostly {what}; only {ratio:.0%} of letters are {exp} (expected {lang_name}).",
                     {"expected_script": exp, "dominant_script": prof.dominant, "expected_ratio": round(ratio, 4), "counts": prof.counts},
                     confidence=0.95)]

    flags: list[Flag] = []
    if exp != "Latin":
        for run in foreign_runs(t, "Latin", exclude=ctx.tgt_regions, min_words=int(ctx.th("latin_run_minor_words"))):
            major = run.words >= ctx.th("latin_run_major_words")
            flags.append(Flag("script", "wrong_language_script", "major" if major else "minor", (run.start, run.end), "chars",
                              f"{run.words} consecutive English words left untranslated: '{t[run.start:run.end][:80]}'.",
                              {"words": run.words, "text": t[run.start:run.end][:200]},
                              confidence=0.85 if major else 0.5, needs_corroboration=not major))
    for other in sorted(s for s in prof.counts if s not in (exp, "Latin", "Other")):
        for run in foreign_runs(t, other, exclude=ctx.tgt_regions, min_words=int(ctx.th("foreign_indic_run_words"))):
            flags.append(Flag("script", "wrong_language_script", "major", (run.start, run.end), "chars",
                              f"{run.words} words in {other} script inside a {lang_name} translation.",
                              {"script": other, "words": run.words}, confidence=0.85))
    return flags


# -------------------- format
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s")


_BULLET = re.compile(r"^\s*[-*+]\s")


_ORDERED = re.compile(r"^\s*\d+[.)]\s")


_FENCE = re.compile(r"^\s*(```|~~~)")


_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


_LINK = re.compile(r"\]\(([^)\s]*)")


_BEGIN = re.compile(r"\\begin\{([^}]+)\}")


_END = re.compile(r"\\end\{([^}]+)\}")


_MAJOR = {"table_pipes", "separator", "fence", "dollar", "double_dollar", "backtick", "links", "latex_env", "math_braces"}


def _lines(text: str) -> list[tuple[int, int]]:
    """(start, end) of every non-empty line."""
    out, pos = [], 0
    for line in text.split("\n"):
        if line.strip():
            s = pos + (len(line) - len(line.lstrip()))
            out.append((s, pos + len(line.rstrip())))
        pos += len(line) + 1
    return out


def _unescaped(line: str, ch: str) -> int:
    return len(re.findall(r"(?<!\\)" + re.escape(ch), line))


def line_features(line: str) -> dict:
    no_dd = line.replace("$$", "")
    h = _HEADING.match(line)
    math = [m for m in re.findall(r"\$([^$]+)\$", no_dd)]
    return {
        "heading": len(h.group(1)) if h else 0,
        "list": "bullet" if _BULLET.match(line) else ("ordered" if _ORDERED.match(line) else ""),
        "table_pipes": _unescaped(line, "|") if line.lstrip().startswith("|") else 0,
        "separator": bool(_SEPARATOR.match(line)) and "|" in line,
        "fence": bool(_FENCE.match(line)),
        "dollar": _unescaped(no_dd, "$"),
        "double_dollar": line.count("$$"),
        "backtick": line.count("`"),
        "links": tuple(_LINK.findall(line)),
        "latex_env": tuple(_BEGIN.findall(line)) + tuple("/" + e for e in _END.findall(line)),
        "math_braces": tuple(m.count("{") - m.count("}") for m in math),
        "bold": line.count("**"),
    }


def _flag_line(t: str, span: tuple[int, int], reasons: list[str], evidence: dict, confidence: float) -> Flag:
    major = any(r.split(":")[0] in _MAJOR for r in reasons)
    return Flag("format", "format", "major" if major else "minor", span, "chars",
                f"Broken structure on line '{t[span[0]:span[1]][:60]}': " + "; ".join(reasons) + ".",
                evidence, confidence=confidence)


def _self_consistency(t: str, lines: list[tuple[int, int]], src_ok: dict) -> list[tuple[tuple[int, int], list[str]]]:
    """Problems detectable inside the translation alone."""
    problems: list[tuple[tuple[int, int], list[str]]] = []
    header_pipes = None
    in_fence_line = None
    for s, e in lines:
        line = t[s:e]
        f = line_features(line)
        reasons = []
        if f["table_pipes"]:
            if header_pipes is None:
                header_pipes = f["table_pipes"]
            elif f["table_pipes"] != header_pipes and src_ok["tables"]:
                reasons.append(f"table_pipes: row has {f['table_pipes']} pipes, table header has {header_pipes}")
        else:
            header_pipes = None
        if f["fence"]:
            in_fence_line = None if in_fence_line else (s, e)
        if f["dollar"] % 2 and src_ok["dollar"]:
            reasons.append(f"dollar: odd number of '$' ({f['dollar']})")
        if f["backtick"] % 2 and src_ok["backtick"]:
            reasons.append(f"backtick: odd number of backticks ({f['backtick']})")
        if any(b != 0 for b in f["math_braces"]) and src_ok["braces"]:
            reasons.append("math_braces: unbalanced braces inside $...$")
        if reasons:
            problems.append(((s, e), reasons))
    if in_fence_line and src_ok["fence"]:
        problems.append((in_fence_line, ["fence: code fence is never closed"]))
    return problems


@register("format", needs=("translation",), description="Markdown, tables, LaTeX, code and links survive translation intact")
def check_format(ctx: CheckContext) -> list[Flag]:
    src, t = ctx.source, ctx.translation
    s_lines, t_lines = _lines(src), _lines(t)
    s_feats = [line_features(src[a:b]) for a, b in s_lines]
    t_feats = [line_features(t[a:b]) for a, b in t_lines]
    has_markup = any(any(v for k, v in f.items()) for f in s_feats + t_feats)
    if not has_markup:
        return []

    flags: list[Flag] = []
    flagged_lines: set[tuple[int, int]] = set()
    aligned = len(s_lines) == len(t_lines)
    ctx.metrics["format"] = {"source_lines": len(s_lines), "translation_lines": len(t_lines), "line_aligned": aligned}

    if aligned:
        for (ts, te), sf, tf in zip(t_lines, s_feats, t_feats, strict=True):
            reasons = [f"{k}: source {sf[k]!r} vs translation {tf[k]!r}" for k in sf if sf[k] != tf[k]]
            if reasons:
                flags.append(_flag_line(t, (ts, te), reasons, {"mode": "line_aligned"}, 0.9))
                flagged_lines.add((ts, te))

    # source-side sanity, so we never blame the system for a source that is itself unbalanced
    src_all = "\n".join(src[a:b] for a, b in s_lines)
    src_ok = {
        "tables": len({f["table_pipes"] for f in s_feats if f["table_pipes"]}) <= 1,
        "dollar": all(f["dollar"] % 2 == 0 for f in s_feats),
        "backtick": all(f["backtick"] % 2 == 0 for f in s_feats),
        "braces": all(all(b == 0 for b in f["math_braces"]) for f in s_feats),
        "fence": sum(f["fence"] for f in s_feats) % 2 == 0,
    }
    for span, reasons in _self_consistency(t, t_lines, src_ok):
        if span not in flagged_lines:
            flags.append(_flag_line(t, span, reasons, {"mode": "self_consistency"}, 0.85))
            flagged_lines.add(span)

    # protected content must be byte-identical (whitespace-insensitive)
    def bodies(text: str) -> Counter:
        return Counter((r.kind, re.sub(r"\s+", " ", text[r.start:r.end]).strip()) for r in find_protected(text) if r.kind != "url")

    src_b, tgt_b = bodies(src_all), bodies(t)
    changed = tgt_b - src_b
    if changed and (src_b - tgt_b):
        for r in find_protected(t):
            key = (r.kind, re.sub(r"\s+", " ", t[r.start:r.end]).strip())
            if changed.get(key, 0) > 0 and not any(a <= r.start < b for a, b in flagged_lines):
                changed[key] -= 1
                flags.append(Flag("format", "format", "major", (r.start, r.end), "chars",
                                  f"{r.kind.replace('_', ' ')} changed: '{t[r.start:r.end][:60]}' does not match the source.",
                                  {"kind": r.kind, "translation": t[r.start:r.end][:200]}, confidence=0.8))

    # last resort when nothing could be localized: whole-text structural deltas
    if not flags:
        keys = ["heading", "table_pipes", "fence", "dollar", "double_dollar", "backtick"]
        deltas = {k: (sum(bool(f[k]) if k in ("heading", "fence") else f[k] for f in s_feats),
                      sum(bool(f[k]) if k in ("heading", "fence") else f[k] for f in t_feats)) for k in keys}
        s_links = Counter(link for f in s_feats for link in f["links"])
        t_links = Counter(link for f in t_feats for link in f["links"])
        diffs = {k: v for k, v in deltas.items() if v[0] != v[1]}
        if s_links != t_links:
            diffs["links"] = (sorted(s_links.elements()), sorted(t_links.elements()))
        if diffs:
            flags.append(Flag("format", "format", "major", None, "chars",
                              "Structure differs from the source: " + "; ".join(f"{k} {a} vs {b}" for k, (a, b) in diffs.items()) + ".",
                              {"mode": "counts", "deltas": {k: list(v) for k, v in diffs.items()}}, confidence=0.6))
    return flags


# -------------------- length
def content_length(text: str) -> int:
    """Characters that carry content: normalized, protected regions and spaces removed."""
    return len(normalize(mask_protected(text)).replace(" ", ""))


def log_length_ratio(source: str, translation: str) -> float | None:
    s, t = content_length(source), content_length(translation)
    if s < 10 or t == 0:  # too short to be meaningful (or empty: the script check owns that)
        return None
    return math.log(t / s)


@register("length_ratio", needs=("translation",), description="Translation length is typical for this language (weak signal)")
def check_length_ratio(ctx: CheckContext) -> list[Flag]:
    ratio = log_length_ratio(ctx.source, ctx.translation)
    if ratio is None:
        return []
    z = ctx.stats.z("log_length_ratio", ctx.lang, ctx.item_id, min_n=int(ctx.th("min_stat_items")))
    ctx.metrics["length_ratio"] = {"ratio": round(math.exp(ratio), 4), "z": None if z is None else round(z, 3)}
    if z is None or abs(z) < ctx.th("robust_z"):
        return []
    short = z < 0
    return [Flag("length_ratio", "omission" if short else "addition", "major", None, "chars",
                 f"Translation is unusually {'short' if short else 'long'} for {ctx.lang} (length ratio {math.exp(ratio):.2f}, robust z {z:+.1f} vs this run).",
                 {"ratio": round(math.exp(ratio), 4), "robust_z": round(z, 3)},
                 confidence=0.4, needs_corroboration=True)]


@register("reference_chrf", needs=("translation", "reference"), description="chrF++ against the reference translation (metric only)")
def check_reference(ctx: CheckContext) -> list[Flag]:
    from sacrebleu.metrics import CHRF

    hyp, ref = normalize(ctx.translation), normalize(ctx.reference)
    if not hyp or not ref:
        return []
    chrf = CHRF(word_order=2).sentence_score(hyp, [ref]).score
    precision = CHRF(word_order=2, beta=1e-6).sentence_score(hyp, [ref]).score  # low → content added
    recall = CHRF(word_order=2, beta=1e6).sentence_score(hyp, [ref]).score  # low → content missing
    ctx.metrics["reference_chrf"] = {"chrf": round(chrf, 2), "precision": round(precision, 2), "recall": round(recall, 2)}
    return []


@register("api_truncation", needs=("translation",), description="Translate API reported the output was cut at its token limit")
def check_api_truncation(ctx: CheckContext) -> list[Flag]:
    if not ctx.translation_meta.get("truncated"):
        return []
    n = len(ctx.translation)
    return [Flag("api_truncation", "omission", "critical", (n, n), "chars",
                 "Indic-Translate stopped at its output-token limit (finish_reason=length); the end of the source is missing.",
                 {"finish_reason": "length"}, confidence=0.97)]


# -------------------- audio
def _merge(spans: list[tuple[float, float]], gap: float = 0.1) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for s, e in sorted(spans):
        if out and s - out[-1][1] <= gap:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def speech_seconds_per_char(ctx: CheckContext) -> float | None:
    a = ctx.audio
    chars = len(normalize(ctx.audio_text or "").replace(" ", ""))
    if a is None or chars < 5:
        return None
    speech = a.duration_s - a.leading_silence_s - a.trailing_silence_s
    return speech / chars if speech > 0 else None


@register("audio_sanity", needs=("audio",), description="Clipping, silences, noise and an abnormal noise floor in the audio")
def check_audio_sanity(ctx: CheckContext) -> list[Flag]:
    a = ctx.audio
    ctx.metrics["audio"] = a.to_metrics()
    flags: list[Flag] = []
    if a.duration_s <= 0.05 or a.voiced_fraction == 0:
        return [Flag("audio_sanity", "speech_garbled", "critical", (0.0, a.duration_s), "seconds",
                     "Audio is empty or completely silent.", {"duration_s": round(a.duration_s, 3)}, confidence=0.95)]
    if a.clip_events:  # one flag per clip; individual events go to evidence
        events = _merge(a.clip_events, gap=0.25)
        sev = "major" if a.clipped_fraction > 0.001 else "minor"
        flags.append(Flag("audio_sanity", "speech_garbled", sev, (events[0][0], events[-1][1]), "seconds",
                          f"Clipping (samples at full scale) in {len(events)} place(s) between {events[0][0]:.2f}s and {events[-1][1]:.2f}s; "
                          f"{a.clipped_fraction:.2%} of samples clipped.",
                          {"clipped_fraction": round(a.clipped_fraction, 6), "events": [[round(x, 3), round(y, 3)] for x, y in events]},
                          confidence=0.85))
    for s, e in a.gaps:
        flags.append(Flag("audio_sanity", "speech_garbled", "major", (s, e), "seconds",
                          f"Silence of {e - s:.1f}s in the middle of speech at {s:.2f}-{e:.2f}s.",
                          {"gap_s": round(e - s, 3)}, confidence=0.85))
    edge = ctx.th("edge_silence_s")
    if a.leading_silence_s >= edge:
        flags.append(Flag("audio_sanity", "speech_garbled", "minor", (0.0, a.leading_silence_s), "seconds",
                          f"{a.leading_silence_s:.1f}s of silence before speech starts.", {}, confidence=0.7))
    if a.trailing_silence_s >= edge:
        flags.append(Flag("audio_sanity", "speech_garbled", "minor", (a.duration_s - a.trailing_silence_s, a.duration_s), "seconds",
                          f"{a.trailing_silence_s:.1f}s of silence after speech ends.", {}, confidence=0.7))
    for s, e in _merge(a.noisy_runs):
        flags.append(Flag("audio_sanity", "speech_garbled", "major", (s, e), "seconds",
                          f"Noise-like, unintelligible stretch at {s:.2f}-{e:.2f}s (flat spectrum, not speech).",
                          {"seconds": round(e - s, 3)}, confidence=0.75))
    z = ctx.stats.z("noise_floor_db", ctx.lang, ctx.item_id, min_n=int(ctx.th("min_stat_items")))
    if z is not None and z > ctx.th("robust_z") and a.floor_db > ctx.th("noise_floor_abs_db") and not a.noisy_runs:
        flags.append(Flag("audio_sanity", "speech_garbled", "major", (0.0, a.duration_s), "seconds",
                          f"Background noise floor {a.floor_db:.0f} dBFS is far above this run's norm (robust z {z:+.1f}).",
                          {"floor_db": round(a.floor_db, 2), "robust_z": round(z, 3)}, confidence=0.7))
    return flags


@register("audio_duration", needs=("audio", "audio_text"), description="Speaking time per character vs this run's norm (weak signal)")
def check_audio_duration(ctx: CheckContext) -> list[Flag]:
    a = ctx.audio
    spc = speech_seconds_per_char(ctx)
    if spc is None:
        return []
    z = ctx.stats.z("speech_s_per_char", ctx.lang, ctx.item_id, min_n=int(ctx.th("min_stat_items")))
    ctx.metrics["audio_duration"] = {"speech_s_per_char": round(spc, 5), "z": None if z is None else round(z, 3), "end_rel_db": round(a.end_rel_db, 2)}
    flags: list[Flag] = []
    if z is not None and z < -ctx.th("robust_z"):
        start = max(0.0, a.duration_s - 1.0)
        flags.append(Flag("audio_duration", "speech_omission_truncation", "major", (start, a.duration_s), "seconds",
                          f"Audio is too short for its text ({spc * 1000:.0f} ms per character, robust z {z:+.1f}); words may be missing.",
                          {"speech_s_per_char": round(spc, 5), "robust_z": round(z, 3)}, confidence=0.45, needs_corroboration=True))
    if z is not None and z > ctx.th("robust_z"):
        flags.append(Flag("audio_duration", "speech_repetition", "major", None, "seconds",
                          f"Audio is too long for its text ({spc * 1000:.0f} ms per character, robust z {z:+.1f}); content may be repeated.",
                          {"speech_s_per_char": round(spc, 5), "robust_z": round(z, 3)}, confidence=0.4, needs_corroboration=True))
    return flags
