"""Error injector.

Creates labelled test cases from clean items so the harness can measure itself
without waiting for the hidden set. Every mutation is programmatic on the
*translation* text — deleting spans, changing digits, perturbing entities,
breaking structure. No LLM ever writes Indian-language text here, which is one
of the assignment's automatic-fail rules.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

from .normalize import to_ascii_digits

SENT_SPLIT = re.compile(r"(?<=[.!?।])\s+")
NUM = re.compile(r"\d")
ENTITY = re.compile(r"[A-Za-z][A-Za-z0-9+#._-]{2,}")
TABLE_ROW = re.compile(r"(?m)^\s*\|.*\|\s*$")


@dataclass
class Injected:
    text: str
    label: dict            # {type, severity, span, note}
    kind: str


def _label(kind: str, severity: str, span, note: str) -> dict:
    return {"type": kind, "severity": severity, "span": list(span) if span else None,
            "note": note, "injected": True}


def inject_number(text: str, rng: random.Random) -> Injected | None:
    hits = list(NUM.finditer(to_ascii_digits(text)))
    if not hits:
        return None
    m = rng.choice(hits)
    d = m.group(0)
    new = str((int(d) + rng.choice([1, 2, 3, 7])) % 10)
    out = text[:m.start()] + new + text[m.end():]
    return Injected(out, _label("number", "critical", (m.start(), m.end()),
                                f"digit {d} -> {new}"), "number")


def inject_omission(text: str, rng: random.Random) -> Injected | None:
    parts = [p for p in SENT_SPLIT.split(text) if p.strip()]
    if len(parts) < 2:
        return None
    idx = rng.randrange(len(parts))
    removed = parts[idx]
    pos = text.find(removed)
    if pos < 0:
        return None
    out = (text[:pos] + text[pos + len(removed):]).strip()
    span = (pos, max(pos + 1, pos + min(len(removed), 40)))
    return Injected(out, _label("omission", "major", span,
                                f"deleted sentence {idx + 1}"), "omission")


def inject_addition(text: str, rng: random.Random) -> Injected | None:
    parts = [p for p in SENT_SPLIT.split(text) if len(p.strip()) > 12]
    if not parts:
        return None
    extra = rng.choice(parts)
    out = text + " " + extra
    span = (len(text) + 1, len(out))
    return Injected(out, _label("addition", "major", span,
                                "appended a sentence not in the source"), "addition")


def inject_repetition(text: str, rng: random.Random) -> Injected | None:
    words = text.split()
    if len(words) < 8:
        return None
    i = rng.randrange(0, max(1, len(words) - 4))
    phrase = " ".join(words[i:i + 3])
    pos = text.find(phrase)
    if pos < 0:
        return None
    out = text[:pos + len(phrase)] + " " + phrase + text[pos + len(phrase):]
    span = (pos, pos + len(phrase) * 2 + 1)
    # A duplicated phrase is content that is not in the source, so the label is
    # "addition" — matching what checks/length.py emits. Labelling it
    # "mistranslation" made the category metrics measure the wrong thing.
    return Injected(out, _label("addition", "major", span,
                                "duplicated phrase (repetition)"), "repetition")


def inject_term(text: str, rng: random.Random) -> Injected | None:
    hits = [m for m in ENTITY.finditer(text) if any(c.isupper() for c in m.group(0))
            or any(c.isdigit() for c in m.group(0))]
    if not hits:
        return None
    m = rng.choice(hits)
    tok = m.group(0)
    if tok.isupper():
        bad = tok[:-1] + ("X" if tok[-1] != "X" else "Y")
    else:
        bad = tok + "s"
    out = text[:m.start()] + bad + text[m.end():]
    return Injected(out, _label("term", "major", (m.start(), m.start() + len(bad)),
                                f"entity {tok} -> {bad}"), "term")


def inject_format(text: str, rng: random.Random) -> Injected | None:
    rows = list(TABLE_ROW.finditer(text))
    if not rows:
        return None
    m = rng.choice(rows)
    broken = m.group(0).replace("|", "")
    out = text[:m.start()] + broken + text[m.end():]
    return Injected(out, _label("format", "major", (m.start(), m.start() + len(broken)),
                                "table row lost its pipes"), "format")


def inject_wrong_script(text: str, source: str) -> Injected | None:
    if not source:
        return None
    return Injected(source, _label("language", "critical", (0, min(len(source), 80)),
                                   "output replaced with the English source"), "language")


INJECTORS = {
    "number": inject_number,
    "omission": inject_omission,
    "addition": inject_addition,
    "repetition": inject_repetition,
    "term": inject_term,
    "format": inject_format,
}


def inject_item(item_id: str, source: str, translation: str, lang: str,
                seed: int = 7, kinds: list[str] | None = None) -> list[dict]:
    """Produce labelled variants of one clean item."""
    rng = random.Random(f"{seed}:{item_id}")
    out: list[dict] = []
    for kind in (kinds or list(INJECTORS) + ["language"]):
        if kind == "language":
            res = inject_wrong_script(translation, source)
        else:
            fn = INJECTORS.get(kind)
            res = fn(translation, rng) if fn else None
        if res is None:
            continue
        out.append({
            "id": f"{item_id}--inj-{kind}",
            "source": source,
            "lang": lang,
            "translation": res.text,
            "labels": [res.label],
            "clean": False,
            "injection": res.kind,
        })
    return out
