"""Error categories and the Candidate record every check emits.

Span convention (half-open character offsets ``[start, end)``):
* translation errors and all speech errors point into ``translation`` (the text that was spoken);
* omissions in translation point into ``source`` (where the missing content is),
  and ``source_span`` is filled for any error whose source side is known.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

TRANSLATION_CATEGORIES = ["omission", "addition", "mistranslation", "number", "term", "format", "wrong_language"]
SPEECH_CATEGORIES = ["speech_omission", "speech_repetition", "speech_garbled"]
CATEGORIES = TRANSLATION_CATEGORIES + SPEECH_CATEGORIES
SEVERITIES = ["minor", "major", "critical"]

ALIASES = {
    "number_or_unit": "number", "numbers": "number", "unit": "number", "date": "number",
    "term_or_entity": "term", "entity": "term", "terminology": "term", "name": "term",
    "wrong_language_or_script": "wrong_language", "wrong_script": "wrong_language", "untranslated": "wrong_language",
    "language": "wrong_language", "script": "wrong_language", "formatting": "format", "markdown": "format", "latex": "format",
    "speech_truncation": "speech_omission", "truncation": "speech_omission", "omission_or_truncation": "speech_omission",
    "repetition": "speech_repetition", "garbled": "speech_garbled", "garbled_audio": "speech_garbled", "noise": "speech_garbled",
    "silence": "speech_garbled",
}


def canonical_category(name: str, area: str | None = None) -> str | None:
    """Map a label's free-form type ('Number or unit', 'Omission' + area 'speech') to a category."""
    key = name.strip().lower().replace("-", "_").replace(" ", "_").replace("/", "_")
    if area and area.strip().lower() == "speech" and not key.startswith("speech_"):
        key = "speech_" + key
    key = ALIASES.get(key, key)
    return key if key in CATEGORIES else None


def span_ref(category: str) -> str:
    """Which text a label/flag span of this category indexes by default."""
    return "source" if category == "omission" else "translation"


@dataclass
class Candidate:
    category: str
    score: float  # 0..1, compared against the per-category threshold
    check: str  # name of the check (or "judge")
    severity: str = "major"
    span: tuple[int, int] | None = None  # offsets into translation
    source_span: tuple[int, int] | None = None  # offsets into source
    evidence: dict = field(default_factory=dict)

    def primary_span(self) -> tuple[int, int] | None:
        return self.source_span if span_ref(self.category) == "source" else self.span

    def to_dict(self) -> dict:
        d = asdict(self)
        d["score"] = round(self.score, 3)
        d["span"] = list(self.span) if self.span else None
        d["source_span"] = list(self.source_span) if self.source_span else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Candidate:
        d = dict(d)
        d["span"] = tuple(d["span"]) if d.get("span") else None
        d["source_span"] = tuple(d["source_span"]) if d.get("source_span") else None
        d.pop("checks", None)
        return cls(**d)


def overlaps(a: tuple[int, int] | None, b: tuple[int, int] | None) -> bool:
    if not a or not b:
        return False
    # Zero-width spans (an insertion point) count as overlapping anything that touches them.
    return a[0] <= b[1] and b[0] <= a[1] if a[0] == a[1] or b[0] == b[1] else a[0] < b[1] and b[0] < a[1]
