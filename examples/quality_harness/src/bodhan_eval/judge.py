"""LLM judge (plan §11): reads source and translation side by side and names each error.

Blind: it sees the texts and the back-translation, never the other checks' findings, so it can't be
anchored by them. The rubric is prompts/judge.md.

The LLM reads Indian-language text but never writes it. Code enforces that: a quote must be found
verbatim in the declared source or translation segment, or it is dropped (`invalid_quote`); offsets
are always computed here; Indian-script words in an explanation that are not copied from the
translation are removed.

ponytail: one call per item, no chunking; items over MAX_CHARS are skipped with a recorded reason.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .checks import Candidate
from .clients import Clients, ModelError
from .langs import script_of
from .segment import segments

log = logging.getLogger("bodhan_eval.judge")

RUBRIC = (Path(__file__).parent / "prompts" / "judge.md").read_text(encoding="utf-8")
MAX_CHARS = 8000
CATEGORIES = {"omission", "addition", "mistranslation", "number", "term", "wrong_language"}
ALIASES = {"number_unit": "number", "numbers": "number", "term_entity": "term", "entity": "term", "terminology": "term",
           "accuracy": "mistranslation", "wrong language": "wrong_language", "untranslated": "wrong_language"}
SEVERITIES = {"critical", "major", "minor"}
CONFIDENCE = {"low": 0.4, "medium": 0.6, "high": 0.8}
LANG_NAMES = {"hi": "Hindi", "ta": "Tamil", "bn": "Bengali", "te": "Telugu", "mr": "Marathi", "gu": "Gujarati",
              "kn": "Kannada", "ml": "Malayalam", "pa": "Punjabi", "or": "Odia", "as": "Assamese", "ur": "Urdu",
              "ne": "Nepali", "sa": "Sanskrit", "kok": "Konkani", "mai": "Maithili", "doi": "Dogri", "brx": "Bodo",
              "sat": "Santali", "ks": "Kashmiri", "sd": "Sindhi", "mni": "Manipuri", "en": "English"}


def judge(source: str, translation: str, back: str | None, lang: str, clients: Clients,
          repeat: int = 0) -> tuple[list[Candidate], dict]:
    """The judge's errors as candidates, and a note of what was dropped and why."""
    sides = {"source": _numbered(source), "translation": _numbered(translation)}
    name = LANG_NAMES.get(lang.split("-")[0], lang)
    listing = lambda prefix, segs: "\n".join(f"{prefix}{i + 1}: {s[2]}" for i, s in enumerate(segs))
    user = (f"Translation language: {name}\n\nENGLISH SOURCE\n{listing('S', sides['source'])}\n\n"
            f"TRANSLATION ({name})\n{listing('T', sides['translation'])}\n\n"
            f"MACHINE BACK-TRANSLATION INTO ENGLISH (may itself be wrong)\n{back or '(not available)'}")
    reply = clients.llm([{"role": "system", "content": RUBRIC}, {"role": "user", "content": user}], repeat)
    errors = reply.get("errors") if isinstance(reply, dict) else None
    if not isinstance(errors, list):
        raise ModelError("judge reply has no 'errors' list")

    texts = {"source": source, "translation": translation}
    out, dropped, stripped = [], [], 0
    for e in errors:
        if not isinstance(e, dict):
            dropped.append({"reason": "not an object"})
            continue
        category = str(e.get("category", "")).strip().lower()
        category = ALIASES.get(category, category)
        if category not in CATEGORIES:
            dropped.append({"reason": f"unknown category {category!r}", "error": e})
            continue
        found = _locate(str(e.get("quote") or ""), str(e.get("side", "")), str(e.get("segment", "")), sides, texts)
        if found is None:
            dropped.append({"reason": "invalid_quote", "error": e})
            continue
        side, span = found
        explanation, n = _english_only(str(e.get("explanation") or ""), translation)
        stripped += n
        severity = str(e.get("severity", "")).lower()
        confidence = str(e.get("confidence", "")).lower()
        out.append(Candidate("judge", category, side, span, CONFIDENCE.get(confidence, 0.6),
                             {"explanation": explanation, "confidence": confidence or None, "segment": e.get("segment"),
                              **({"repeat": repeat} if repeat else {})},
                             severity if severity in SEVERITIES else "major"))
    if dropped:
        log.info("judge: dropped %d of %d errors (%s)", len(dropped), len(errors), ", ".join(d["reason"] for d in dropped))
    if stripped:
        log.warning("judge: removed %d non-English word(s) from explanations", stripped)
    return out, {"errors": len(errors), "kept": len(out), "dropped": dropped, "stripped_words": stripped}


def _numbered(text):
    """(start, end, text) per segment; the whole text when it has no segments (e.g. only a formula)."""
    segs = [(s.start, s.end, s.text) for s in segments(text)]
    return segs or [(0, len(text), text)]


def _locate(quote, side, segment, sides, texts):
    """(side, span) of the quote: in the named segment, else anywhere on that side, else on the other side."""
    q = quote.strip().strip("\"'“”‘’").strip()
    if not q:
        return None
    side = side if side in sides else "translation"
    m = re.fullmatch(r"[ST]?(\d+)", segment.strip().upper())
    k = int(m.group(1)) - 1 if m else -1
    tries = []
    if 0 <= k < len(sides[side]):
        start, end, _ = sides[side][k]
        tries.append((side, start, end))
    other = "source" if side == "translation" else "translation"
    tries += [(side, 0, len(texts[side])), (other, 0, len(texts[other]))]
    loose = r"\s+".join(map(re.escape, q.split()))  # same words, any whitespace between them
    for s, a, b in tries:
        hay = texts[s][a:b]
        i = hay.find(q)
        if i >= 0:
            return s, (a + i, a + i + len(q))
        found = re.search(loose, hay)
        if found:
            return s, (a + found.start(), a + found.end())
    return None


def _english_only(text, translation):
    """Drop words in a non-Latin script unless they are copied from the translation (quoting is allowed)."""
    words, removed = [], 0
    for w in text.split(" "):
        core = w.strip(".,;:!?()[]\"'“”‘’")
        if any(script_of(ch) not in (None, "LATIN") for ch in core) and core not in translation:
            removed += 1
            w = "[…]"
        words.append(w)
    return " ".join(words), removed
