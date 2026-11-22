"""Terms that must be rendered the same way everywhere in the dub.

The LLM reads the cleaned English and lists names, acronyms, technical terms and
figure labels. Code then keeps only terms that literally occur in the text, so a
hallucinated term can never reach translation. A user `--glossary` YAML is merged
in and always wins.

Everything here is English. How each term is rendered per language is decided on
Day 4, by Indic-Translate, never by the LLM.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from .llm import LLM, LLMError
from .repair import Unit

log = logging.getLogger(__name__)

KINDS = ("name", "acronym", "technical", "label")
MAX_TERMS = 80

SYSTEM_PROMPT = """You build a terminology list for dubbing an English video into Indian languages.

From the transcript, list the terms a translator must render the same way every time:
- "name": people, places, organisations, products
- "acronym": abbreviations such as NASA, GDP, CPU
- "technical": domain terms with a precise meaning (e.g. "photosynthesis", "interest rate", "API")
- "label": letters or symbols naming things in a figure or formula (e.g. "XYZ", "PQ", "x")

Copy each term exactly as it appears in the transcript. Skip everyday words. At most 80 terms.

Reply with JSON only: {"terms": [{"term": "photosynthesis", "kind": "technical"}]}"""


@dataclass(frozen=True)
class Term:
    term: str
    kind: str
    keep: bool = False  # user-set: never translate (brands, names that must stay as-is)
    source: str = "llm"  # "llm" or "user"
    occurrences: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class GlossaryError(ValueError):
    pass


def count_occurrences(term: str, text: str) -> int:
    # Word-bounded and case-sensitive for short labels ("PQ" must not match "pq-tree"),
    # case-insensitive for longer terms ("Interest rate" at a sentence start still counts).
    flags = 0 if len(term) <= 3 else re.IGNORECASE
    return len(re.findall(rf"(?<!\w){re.escape(term)}(?!\w)", text, flags))


def load_user_glossary(path: Path) -> list[Term]:
    """Parse `terms: [{term, kind?, keep?}]`. Raise GlossaryError with a precise message on bad input."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise GlossaryError(f"{path}: not valid YAML: {exc}") from exc
    items = data.get("terms") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise GlossaryError(f"{path}: expected a top-level 'terms:' list")
    terms: list[Term] = []
    for n, item in enumerate(items):
        if isinstance(item, str):
            item = {"term": item}
        if not isinstance(item, dict) or not str(item.get("term") or "").strip():
            raise GlossaryError(f"{path}: terms[{n}] needs a non-empty 'term'")
        kind = str(item.get("kind") or "technical")
        if kind not in KINDS:
            raise GlossaryError(f"{path}: terms[{n}].kind must be one of {', '.join(KINDS)}")
        terms.append(Term(term=str(item["term"]).strip(), kind=kind, keep=bool(item.get("keep", False)),
                          source="user"))
    return terms


def filter_llm_terms(reply: object, text: str) -> list[Term]:
    """Keep well-formed terms that really occur in the text; dedupe case-insensitively."""
    items = reply.get("terms") if isinstance(reply, dict) else None
    if not isinstance(items, list):
        raise LLMError('glossary reply must be {"terms": [...]}')
    seen: set[str] = set()
    terms: list[Term] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term") or "").strip()
        kind = str(item.get("kind") or "")
        if not term or kind not in KINDS or term.lower() in seen:
            continue
        hits = count_occurrences(term, text)
        if hits == 0:
            log.info("glossary: dropped term not found in transcript", extra={"stage": "glossary", "term": term})
            continue
        seen.add(term.lower())
        terms.append(Term(term=term, kind=kind, occurrences=hits))
    return terms[:MAX_TERMS]


def build_glossary(llm: LLM | None, units: Sequence[Unit], user_terms: Sequence[Term] = ()) -> list[Term]:
    text = "\n".join(u.text for u in units if not u.dropped)
    llm_terms: list[Term] = []
    if llm is not None and text.strip():
        try:
            llm_terms = filter_llm_terms(llm.complete_json(SYSTEM_PROMPT, text), text)
        except LLMError as exc:
            # A glossary is a quality aid, not a requirement: dub without one rather than fail.
            log.warning("glossary: LLM extraction failed; continuing with user terms only",
                        extra={"stage": "glossary", "error": str(exc)})
    merged = {t.term.lower(): Term(t.term, t.kind, t.keep, "user", count_occurrences(t.term, text))
              for t in user_terms}
    for t in llm_terms:
        merged.setdefault(t.term.lower(), t)
    terms = sorted(merged.values(), key=lambda t: (-t.occurrences, t.term.lower()))
    log.info("glossary built", extra={"stage": "glossary", "terms": len(terms),
                                      "user": sum(t.source == "user" for t in terms)})
    return terms
