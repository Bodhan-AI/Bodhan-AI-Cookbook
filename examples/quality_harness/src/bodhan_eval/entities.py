"""Entity / term extraction from English source text (string-based, no learned NER).

Produces candidate terms that should survive translation: acronyms, proper-noun sequences,
quoted terms, and alphanumeric tokens like "H2O". Each term keeps its source span.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Span

_ACRONYM = re.compile(r"\b[A-Z]{2,}(?:\d+)?\b")
_PROPER = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b")
_QUOTED = re.compile(r"\"([^\"]{2,40})\"|'([^']{2,40})'")
_ALNUM = re.compile(r"\b[A-Za-z]+\d[A-Za-z0-9]*\b")

_STOPWORD_PROPER = {"The", "A", "An", "In", "On", "At", "It", "This", "That", "They"}


@dataclass
class Term:
    text: str
    span: Span
    kind: str  # acronym | proper | quoted | alnum


def extract_terms(source: str) -> list[Term]:
    terms: list[Term] = []
    seen: set[tuple[str, int]] = set()

    def add(text: str, span: Span, kind: str) -> None:
        key = (text, span[0])
        if key in seen or not text.strip():
            return
        seen.add(key)
        terms.append(Term(text=text.strip(), span=span, kind=kind))

    for m in _ACRONYM.finditer(source):
        add(m.group(), (m.start(), m.end()), "acronym")
    for m in _ALNUM.finditer(source):
        add(m.group(), (m.start(), m.end()), "alnum")
    for m in _QUOTED.finditer(source):
        g = m.group(1) or m.group(2) or ""
        add(g, (m.start(), m.end()), "quoted")
    for m in _PROPER.finditer(source):
        word = m.group()
        if word in _STOPWORD_PROPER:
            continue
        # skip a capitalized word that merely starts a sentence (prev non-space is . ? !)
        prev = source[: m.start()].rstrip()
        if (not prev or prev[-1] in ".!?।") and " " not in word:
            continue
        add(word, (m.start(), m.end()), "proper")
    return terms
