"""Terminology that must be rendered the same way every time.

Important design note: ``indic-translate`` exposes no glossary or context
parameter — it takes text and a target language, nothing else. So a glossary
cannot be *enforced*. Claiming otherwise would be dishonest.

What it can do is three real things:

1. **protect** — the list is handed to the LLM whenever it condenses English, so
   a protected term is never edited out;
2. **verify** — after translation, deterministic matching reports any term whose
   rendering is missing from a segment that clearly contains the English term;
3. **report** — every mismatch lands in ``segments.<lang>.json`` and the run
   report, with the segments named.

Matching tolerates one inflectional character at the end, because Indic nouns
change form with case (`समकोण` / `समकोणों`). It is approximate on purpose and
says so; a language-specific stemmer would be better and is out of scope.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Term:
    en: str
    targets: dict[str, str]


class Glossary:
    def __init__(self, terms: list[Term] | None = None) -> None:
        self.terms: tuple[Term, ...] = tuple(terms or ())

    def __len__(self) -> int:
        return len(self.terms)

    @classmethod
    def empty(cls) -> Glossary:
        return cls([])

    @classmethod
    def load(cls, path: Path | str | None) -> Glossary:
        if path is None:
            return cls.empty()
        file = Path(path)
        if not file.exists():
            return cls.empty()
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - dependency is pinned
            raise RuntimeError("PyYAML is required to read a glossary file") from exc

        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        raw_terms = data.get("terms") or []
        terms: list[Term] = []
        for entry in raw_terms:
            if not isinstance(entry, dict):
                continue
            en = str(entry.get("en", "")).strip()
            if not en:
                continue
            targets = {
                str(key): str(value)
                for key, value in entry.items()
                if key != "en" and value is not None and str(value).strip()
            }
            terms.append(Term(en=en, targets=targets))
        return cls(terms)

    # -- lookups ----------------------------------------------------------

    def for_language(self, lang: str) -> dict[str, str]:
        return {term.en: term.targets[lang] for term in self.terms if lang in term.targets}

    def to_dict(self) -> list[dict[str, Any]]:
        return [{"en": term.en, "targets": dict(term.targets)} for term in self.terms]

    def terms_in(self, text: str) -> list[str]:
        lowered = text.casefold()
        return [term.en for term in self.terms if term.en.casefold() in lowered]

    # -- verification -----------------------------------------------------

    def check(
        self,
        sources: dict[int, str],
        translations: dict[int, str],
        lang: str,
    ) -> list[dict[str, Any]]:
        """Report terms whose expected rendering is missing where it should appear."""
        targets = self.for_language(lang)
        issues: list[dict[str, Any]] = []
        for english, expected in targets.items():
            hits = [sid for sid, text in sources.items() if english.casefold() in text.casefold()]
            if not hits:
                continue
            missing = [
                sid for sid in hits if not _renders(translations.get(sid, ""), expected)
            ]
            if missing:
                issues.append(
                    {
                        "term": english,
                        "expected": expected,
                        "segments_with_term": sorted(hits),
                        "segments_missing": sorted(missing),
                    }
                )
        return issues


def _normalise(text: str) -> str:
    return _WS.sub("", text).casefold()


def _renders(translation: str, expected: str) -> bool:
    haystack = _normalise(translation)
    needle = _normalise(expected)
    if not needle or not haystack:
        return False
    if needle in haystack:
        return True
    # Tolerate one trailing inflectional character (case endings, plural markers).
    return len(needle) > 3 and needle[:-1] in haystack
