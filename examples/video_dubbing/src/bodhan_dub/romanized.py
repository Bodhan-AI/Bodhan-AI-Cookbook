"""English glosses for romanized Indian-language phrases inside English speech.

Indian English often mixes in romanized Hindi (or another language): "It is ekdum mast and
bilkul simple, chalo shuru karte hain." Indic-Translate renders that naturally into Hindi, but
into Tamil it only spells the Hindi words in Tamil script, so a Tamil listener hears Hindi, not
meaning (seen live). So the LLM, editing only the English side as the rules allow, writes a
gloss: the same line with just those phrases replaced by plain English. The original line is
translated for the phrases' own language; every other language gets the gloss.

Code checks each gloss (numbers and glossary terms kept, the speaker's other words unchanged)
and drops any that fail, so a bad gloss can never change the meaning of a line.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

from . import textcheck
from .glossary import Term
from .llm import LLM, LLMError
from .repair import Unit, fidelity

log = logging.getLogger(__name__)

BATCH = 30
# Romanized Hindi is also natural for Urdu listeners (one spoken language, two scripts).
SAME_SPEECH = {"hi": {"hi", "ur"}, "ur": {"hi", "ur"}}
MIN_KEPT = 0.5  # share of the line's words a gloss must keep: it replaces phrases, not the line

SYSTEM_PROMPT = """You find romanized (Latin-script) Indian-language words and phrases inside an English video transcript, the way Indian English speakers mix them in, e.g. Hindi "ekdum mast", "chalo shuru karte hain", "bilkul", Tamil "romba nalla", Bengali "khub bhalo".

Do NOT treat these as phrases to gloss: names of people and places, foods, festivals, brands, honorifics used as names (e.g. "Priya didi"), and loanwords established in English (e.g. "yoga", "guru", "paneer tikka", "Diwali").

For every unit that contains such phrases, return the full unit text with ONLY those phrases replaced by their plain English meaning. Keep every other word, number and name exactly as it is. Also give the ISO 639-1 code of the phrases' language.
Skip units that contain none.

Reply with JSON only: {"glosses": [{"id": "u0003", "language": "hi", "text": "..."}]}"""


def check_gloss(original: str, gloss: str, terms: Sequence[Term]) -> str:
    """'' if the gloss is acceptable, else why it is rejected."""
    if not gloss.strip() or gloss.strip() == original.strip():
        return "empty or unchanged"
    if lost := textcheck.missing_numbers(original, gloss):
        return f"dropped numbers {lost}"
    dropped = [t.term for t in terms if textcheck.term_in(t.term, t.kind, original)
               and not textcheck.term_in(t.term, t.kind, gloss)]
    if dropped:
        return f"dropped glossary terms {dropped}"
    if not 0.5 <= len(gloss) / max(1, len(original)) <= 2.0:
        return "length changed too much"
    if fidelity(original, gloss) < MIN_KEPT:
        return "rewrote more than the romanized phrases"
    return ""


def add_glosses(llm: LLM | None, units: Sequence[Unit], terms: Sequence[Term]) -> int:
    """Fill `unit.gloss` / `unit.gloss_lang` where the LLM finds romanized phrases. Returns the count."""
    if llm is None:
        return 0
    live = [u for u in units if not u.dropped and u.text.strip()]
    by_id = {u.id: u for u in live}
    added = 0
    for i in range(0, len(live), BATCH):
        batch = live[i:i + BATCH]
        try:
            reply = llm.complete_json(SYSTEM_PROMPT, json.dumps(
                {"units": [{"id": u.id, "text": u.text} for u in batch]}, ensure_ascii=False))
        except LLMError as exc:
            log.warning("gloss: LLM failed; lines keep their original wording",
                        extra={"stage": "gloss", "error": str(exc)})
            continue
        items = reply.get("glosses") if isinstance(reply, dict) else None
        for item in items if isinstance(items, list) else []:
            unit = by_id.get(str(item.get("id"))) if isinstance(item, dict) else None
            if unit is None:
                continue
            gloss, lang = str(item.get("text") or "").strip(), str(item.get("language") or "").strip().lower()
            why = check_gloss(unit.text, gloss, terms)
            if why:
                log.info("gloss rejected", extra={"stage": "gloss", "unit": unit.id, "reason": why})
                continue
            unit.gloss, unit.gloss_lang = gloss, lang or None
            added += 1
            log.info("gloss added", extra={"stage": "gloss", "unit": unit.id, "language": unit.gloss_lang})
    return added


def text_for(unit: Unit, target: str) -> str:
    """The English a target language should translate: the original wording for the phrases' own
    language (it renders them natively), the English gloss for every other language."""
    if unit.gloss and target not in SAME_SPEECH.get(unit.gloss_lang or "", {unit.gloss_lang}):
        return unit.gloss
    return unit.text
