"""Programmatic translation errors. Each op returns an Injection or None when it does not apply.

No op writes new target-language text: every change deletes, moves, copies or mutates text that
already exists (in this item, another item, or a parallel translation from the same corpus).
"""

from __future__ import annotations

import random
import unicodedata
from dataclasses import dataclass, field

import regex

from ..languages import script_of
from ..text import markup
from ..text.normalize import fold_digits
from ..text.segment import clauses, sentences, words


@dataclass
class Injection:
    translation: str
    label: dict
    note: str
    extra: dict = field(default_factory=dict)


def _label(kind: str, severity: str, span: tuple[int, int], field: str = "translation", **extra) -> dict:
    return {"type": kind, "severity": severity, "span": [span[0], span[1]], "field": field, "annotator": "injector", **extra}


def omission(source: str, translation: str, rng: random.Random, **_) -> Injection | None:
    src_s, tgt_s = sentences(source), sentences(translation)
    if len(tgt_s) >= 2 and len(src_s) == len(tgt_s):
        k = rng.randrange(len(tgt_s))
        cut = tgt_s[k]
        end = tgt_s[k + 1].start if k + 1 < len(tgt_s) else len(translation)
        start = cut.start if k + 1 < len(tgt_s) else (tgt_s[k - 1].end if k > 0 else cut.start)
        new = translation[:start] + translation[end:]
        sev = "critical" if len(cut.text) > 0.45 * len(translation) else "major"
        return Injection(
            new,
            _label("omission", sev, (src_s[k].start, src_s[k].end), field="source", translation_point=start),
            f"deleted translation sentence {k + 1} of {len(tgt_s)}",
        )
    src_c, tgt_c = clauses(source), clauses(translation)
    candidates = [i for i, c in enumerate(tgt_c) if len(words(c.text)) >= 3 and i > 0]
    if not candidates:
        return None
    k = rng.choice(candidates)
    cut = tgt_c[k]
    start = tgt_c[k - 1].end
    new = translation[:start] + translation[cut.end :]
    if len(src_c) == len(tgt_c):
        return Injection(new, _label("omission", "major", (src_c[k].start, src_c[k].end), field="source", translation_point=start), f"deleted clause {k + 1}")
    return Injection(new, _label("omission", "major", (start, start)), f"deleted clause {k + 1} (no source alignment)")


def addition(source: str, translation: str, rng: random.Random, pool: list[str] | None = None, **_) -> Injection | None:
    donors = [s for text in (pool or []) for s in (p.text for p in sentences(text)) if 20 <= len(s) <= 160 and s not in translation]
    if not donors:
        return None
    extra = rng.choice(donors)
    tgt_s = sentences(translation)
    if len(tgt_s) >= 2 and rng.random() < 0.5:
        k = rng.randrange(1, len(tgt_s))
        pos = tgt_s[k].start
        new = translation[:pos] + extra + " " + translation[pos:]
        span = (pos, pos + len(extra))
    else:
        sep = "" if translation.endswith((" ", "\n")) else " "
        new = translation + sep + extra
        span = (len(translation) + len(sep), len(new))
    return Injection(new, _label("addition", "major", span), "inserted a sentence from another item")


def mistranslation(source: str, translation: str, rng: random.Random, pool: list[str] | None = None, lang_script: str | None = None, **_) -> Injection | None:
    def native(w: str) -> bool:
        letters = [ch for ch in w if unicodedata.category(ch).startswith("L")]
        return len(w) >= 4 and bool(letters) and all(script_of(ch) == lang_script for ch in letters) and not regex.search(r"\d", fold_digits(w))

    targets = [w for w in words(translation) if native(w.text)]
    vocab = sorted({w.text for text in (pool or []) for w in words(text) if native(w.text)} - {w.text for w in words(translation)})
    if not targets or not vocab:
        return None
    victim = rng.choice(targets)
    close = [v for v in vocab if abs(len(v) - len(victim.text)) <= 2] or vocab
    replacement = rng.choice(close)
    new = translation[: victim.start] + replacement + translation[victim.end :]
    return Injection(new, _label("mistranslation", "major", (victim.start, victim.start + len(replacement))), "replaced a content word with an unrelated word")


def number(source: str, translation: str, rng: random.Random, **_) -> Injection | None:
    folded = fold_digits(translation)
    found = [m for m in regex.finditer(r"\d+", folded)]
    if not found:
        return None
    m = rng.choice(found)
    digits = m.group(0)
    value = int(digits)
    choice = rng.randrange(3)
    if choice == 0 and len(digits) >= 2 and len(set(digits)) > 1:
        chars = list(digits)
        i = rng.randrange(len(chars) - 1)
        while chars[i] == chars[i + 1]:
            i = (i + 1) % (len(chars) - 1)
        chars[i], chars[i + 1] = chars[i + 1], chars[i]
        new_digits = "".join(chars)
    elif choice == 1 and value > 1:
        new_digits = str(value * 10)
    else:
        delta = rng.choice([1, 2, 3, 5])
        new_digits = str(value - delta if value - delta >= 0 and rng.random() < 0.5 else value + delta)
    original = translation[m.start() : m.end()]
    native_zero = next((ch for ch in original if unicodedata.decimal(ch, None) is not None and not ch.isascii()), None)
    if native_zero:
        base = ord(native_zero) - unicodedata.decimal(native_zero)
        new_digits = "".join(chr(base + int(d)) for d in new_digits)
    new = translation[: m.start()] + new_digits + translation[m.end() :]
    return Injection(new, _label("number", "major", (m.start(), m.start() + len(new_digits))), f"changed number {original} to {new_digits}")


def term(source: str, translation: str, rng: random.Random, term_span: tuple[int, int] | None = None, lang_script: str | None = None, **_) -> Injection | None:
    """Corrupt a name or term. `term_span` comes from an aligner; otherwise a Latin-script term shared with the source is used."""
    span = term_span
    if span is None:
        src_words = {w.text for w in words(source)}
        shared = [w for w in words(translation) if regex.fullmatch(r"[A-Za-z][A-Za-z0-9\-]{2,}", w.text) and w.text in src_words]
        if not shared:
            return None
        w = rng.choice(shared)
        span = (w.start, w.end)
    token = translation[span[0] : span[1]]
    positions = [i for i, ch in enumerate(token) if unicodedata.category(ch) == "Lo" or ch.isalpha()]
    if not positions:
        return None
    i = rng.choice(positions)
    ch = token[i]
    if ch.isascii():
        pool = "bcdfghklmnprstvz" if ch.lower() not in "aeiou" else "aeiou"
        new_ch = rng.choice([c for c in pool if c != ch.lower()])
        new_ch = new_ch.upper() if ch.isupper() else new_ch
    else:
        script = script_of(ch)
        options = [chr(ord(ch) + d) for d in (-3, -2, -1, 1, 2, 3) if unicodedata.category(chr(ord(ch) + d)) == "Lo" and script_of(chr(ord(ch) + d)) == script]
        if not options:
            return None
        new_ch = rng.choice(options)
    new_token = token[:i] + new_ch + token[i + 1 :]
    new = translation[: span[0]] + new_token + translation[span[1] :]
    return Injection(new, _label("term", "major", (span[0], span[0] + len(new_token))), f"misspelled term {token!r}")


def format_error(source: str, translation: str, rng: random.Random, **_) -> Injection | None:
    ops = []
    tlines = markup.lines(translation)
    rows = [ln for ln in tlines if markup.signature(ln.text)[0] == "table_row"]
    seps = [ln for ln in tlines if markup.signature(ln.text)[0] == "table_sep"]
    maths = markup.math_segments(translation)
    heads = [ln for ln in tlines if markup.signature(ln.text)[0] == "heading"]
    items = [ln for ln in tlines if markup.signature(ln.text)[0] == "list_item"]
    if rows:
        ops.append("drop_cell")
    if seps:
        ops.append("drop_separator")
    if maths:
        ops += ["break_math", "alter_math"]
    if heads:
        ops.append("drop_heading")
    if items:
        ops.append("merge_list_item")
    if "**" in translation:
        ops.append("unpair_bold")
    if not ops:
        return None
    op = rng.choice(sorted(ops))
    if op == "drop_cell":
        ln = rng.choice(rows)
        last = ln.text.rfind("|", 0, len(ln.text.rstrip()) - 1)
        if last <= 0:
            return None
        new_line = ln.text[:last] + ln.text[ln.text.rfind("|") :]
        new = translation[: ln.start] + new_line + translation[ln.end :]
        return Injection(new, _label("format", "major", (ln.start, ln.start + len(new_line))), "removed a table cell")
    if op == "drop_separator":
        ln = rng.choice(seps)
        end = ln.end + 1 if ln.end < len(translation) and translation[ln.end] == "\n" else ln.end
        new = translation[: ln.start] + translation[end:]
        return Injection(new, _label("format", "major", (max(ln.start - 1, 0), min(ln.start + 1, len(new)))), "deleted the table separator row")
    if op == "break_math":
        seg = rng.choice(maths)
        close = seg.end - (2 if seg.display else 1)
        new = translation[:close] + translation[seg.end :]
        return Injection(new, _label("format", "major", (seg.start, close)), "removed the closing math delimiter")
    if op == "alter_math":
        seg = rng.choice(maths)
        body = translation[seg.start : seg.end]
        swaps = [("+", "-"), ("=", "+"), ("^2", "^3"), ("\\frac", "\\dfrac{}"), ("a", "b"), ("x", "y")]
        for old, repl in rng.sample(swaps, len(swaps)):
            if old in body:
                new_body = body.replace(old, repl, 1)
                new = translation[: seg.start] + new_body + translation[seg.end :]
                return Injection(new, _label("format", "major", (seg.start, seg.start + len(new_body))), f"changed LaTeX {old!r} to {repl!r}")
        return None
    if op == "drop_heading":
        ln = rng.choice(heads)
        m = markup.HEADING.match(ln.text)
        new = translation[: ln.start] + ln.text[m.end() :] + translation[ln.end :]
        return Injection(new, _label("format", "minor", (ln.start, ln.start + len(ln.text) - m.end())), "removed a heading marker")
    if op == "merge_list_item":
        ln = rng.choice(items)
        if ln.start == 0:
            return None
        m = markup.LIST_ITEM.match(ln.text)
        new = translation[: ln.start - 1] + " " + ln.text[m.end() :] + translation[ln.end :]
        return Injection(new, _label("format", "major", (ln.start - 1, ln.start + len(ln.text) - m.end())), "merged a list item into the previous line")
    pos = translation.find("**")
    new = translation[:pos] + translation[pos + 2 :]
    return Injection(new, _label("format", "minor", (max(pos - 1, 0), pos + 1)), "removed one bold marker")


def wrong_language(source: str, translation: str, rng: random.Random, alternates: dict[str, str] | None = None, **_) -> Injection | None:
    """Swap in the same content from another language (whole item) or leave one sentence in English."""
    src_s, tgt_s = sentences(source), sentences(translation)
    options = []
    if alternates:
        options.append("other_language")
    if len(tgt_s) >= 2 and len(src_s) == len(tgt_s):
        options.append("untranslated_sentence")
    if not options:
        return None
    op = rng.choice(options)
    if op == "other_language":
        code = rng.choice(sorted(alternates))
        text = alternates[code]
        return Injection(text, _label("wrong_language", "critical", (0, len(text)), other_lang=code), f"replaced with the {code} translation")
    k = rng.randrange(len(tgt_s))
    english = src_s[k].text
    new = translation[: tgt_s[k].start] + english + translation[tgt_s[k].end :]
    return Injection(new, _label("wrong_language", "major", (tgt_s[k].start, tgt_s[k].start + len(english))), f"left sentence {k + 1} in English")


OPS = {
    "omission": omission,
    "addition": addition,
    "mistranslation": mistranslation,
    "number": number,
    "term": term,
    "format": format_error,
    "wrong_language": wrong_language,
}


def applicable(op: str, source: str, translation: str) -> bool:
    if op == "format":
        return markup.has_markup(translation)
    if op == "number":
        return bool(regex.search(r"\d", fold_digits(translation)))
    return True
