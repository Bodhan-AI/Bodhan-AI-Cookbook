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
from dataclasses import asdict, dataclass, field, replace
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
    # User-supplied expected renderings, lang -> text ("*" = any language whose script it is in).
    # Verify-only: translations are checked against them and mismatches reported; they are never
    # inserted, so every Indian-language word still comes from Indic-Translate.
    renderings: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def count_occurrences(term: str, text: str) -> int:
    # Word-bounded and case-sensitive for short labels ("PQ" must not match "pq-tree"),
    # case-insensitive for longer terms ("Interest rate" at a sentence start still counts).
    flags = 0 if len(term) <= 3 else re.IGNORECASE
    return len(re.findall(rf"(?<!\w){re.escape(term)}(?!\w)", text, flags))


def load_user_glossary(path: Path) -> tuple[list[Term], list[str]]:
    """Read a user glossary in any reasonable shape. Returns (terms, warnings); never raises.

    The brief does not fix a format, so all of these work:
        terms: [linear pair, {term: Bodhan, keep: true}, {term: GDP, kind: acronym}]
        - linear pair                       # a bare list
        linear pair: {hi: रैखिक युग्म}      # term -> expected renderings per language (checked, not inserted)
        Bodhan: keep                        # term -> "keep", a kind, or a rendering
    An unreadable file or entry becomes a warning in report.json, never a failed run.
    """
    warnings: list[str] = []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return [], [f"glossary {path.name}: unreadable, ignored ({str(exc).splitlines()[0][:120]})"]
    if isinstance(data, dict) and "terms" in data:
        data = data["terms"]
    if isinstance(data, dict):
        items: list[object] = [{"term": k, **_mapping_value(v)} for k, v in data.items()]
    elif isinstance(data, list):
        items = data
    elif data is None:
        return [], [f"glossary {path.name}: empty"]
    else:
        return [], [f"glossary {path.name}: expected a list or mapping of terms, got {type(data).__name__}; ignored"]
    terms: list[Term] = []
    for n, item in enumerate(items):
        if isinstance(item, str):
            item = {"term": item}
        term = str(item.get("term") or "").strip() if isinstance(item, dict) else ""
        if not term:
            warnings.append(f"glossary {path.name}: entry {n} has no term; skipped")
            continue
        kind = str(item.get("kind") or "technical").lower()
        if kind not in KINDS:
            warnings.append(f"glossary {path.name}: '{term}' has unknown kind {kind!r}; using 'technical'")
            kind = "technical"
        renderings = item.get("renderings") or item.get("translations") or {}
        if isinstance(renderings, str):
            renderings = {"*": renderings}
        terms.append(Term(term=term, kind=kind, keep=bool(item.get("keep", False)), source="user",
                          renderings={str(k): str(v) for k, v in renderings.items()} if isinstance(renderings, dict)
                          else {}))
    return terms, warnings


def _mapping_value(value: object) -> dict:
    """`term: value` -> fields. value may be None, "keep", a kind, a rendering, a per-language dict or full fields."""
    if value is None:
        return {}
    if isinstance(value, bool):
        return {"keep": value}
    if isinstance(value, str):
        v = value.strip()
        if v.lower() == "keep":
            return {"keep": True}
        if v.lower() in KINDS:
            return {"kind": v.lower()}
        return {"renderings": {"*": v}}
    if isinstance(value, dict):
        if set(value) & {"kind", "keep", "renderings", "translations"}:
            return dict(value)
        return {"renderings": value}  # {hi: ..., ta: ...}
    return {}


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
    merged = {t.term.lower(): replace(t, source="user", occurrences=count_occurrences(t.term, text))
              for t in user_terms}
    for t in llm_terms:
        merged.setdefault(t.term.lower(), t)
    terms = sorted(merged.values(), key=lambda t: (-t.occurrences, t.term.lower()))
    log.info("glossary built", extra={"stage": "glossary", "terms": len(terms),
                                      "user": sum(t.source == "user" for t in terms)})
    return terms
