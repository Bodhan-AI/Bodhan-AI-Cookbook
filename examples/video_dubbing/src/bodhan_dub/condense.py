"""Shorten an English unit with the LLM so its dub fits the time slot. English side only.

The LLM never writes Indian-language text: the shortened English goes back through Indic-Translate.
Every candidate passes two checks: a rule check (shorter, no new numbers, few new words) and an LLM
meaning check (YES/NO). Up to three candidates per call: the second is told why the first was rejected, and
the third aims at a milder target (only filler and minor words removed), because a partial gain still shrinks
an overrun. An LLM *error* is never counted as a NO answer: it ends the call without a result and is counted
separately. Results are cached on disk (prompt + input), so a re-run gives the same text.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from pathlib import Path
from typing import Callable

from .config import get_llm_config, load_env
from .glossary import match_en
from .llm import chat

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You shorten English sentences for video dubbing so that the translated speech fits a time slot.

Rules:
1. Keep the meaning, all numbers, names and technical terms. Do not add any information.
2. Remove filler words, repetitions and optional detail; use shorter wording.
3. Keep it one natural, grammatical sentence (or the same number of sentences as the input).
4. Aim for at most the target number of characters, but never destroy the meaning.
5. Output ONLY the shortened English text, nothing else."""

# The dub has to fit a shorter slot, so the judge must accept the loss of minor detail. The earlier judge
# ("NO if any important idea is changed, lost or added") said NO to "A bright waxing moon" -> "A waxing moon"
# and to "major damage" -> "damage" (measured 2026-11-20); 21 of 29 overruns came from rejected shortenings.
JUDGE_PROMPT = """You check shortened English sentences for video dubbing. The dub must fit a shorter time slot, so
some words have to go.
Answer YES if the SHORTENED sentence keeps the main message and every name, number and key technical term.
Dropping adjectives, intensifiers, filler, repetition or minor detail is fine.
Answer NO if the meaning changes or is reversed, who does what changes, a name or number is lost or altered,
new information is added, or the main point is lost.
Reply with exactly one word: YES or NO."""

MIN_KEEP = 0.35       # result must keep at least this share of the original length
MAX_NEW_WORDS = 0.35  # at most this share of result words may be absent from the original ...
MIN_NEW_ALLOWED = 3   # ... but short sentences may always use 3 new words (any rewording needs some)
MAX_TRIES = 3         # candidates per call: target, target + feedback, milder target

STATS = {"calls": 0, "cached": 0, "accepted": 0, "rejected": 0, "llm_errors": 0, "reasons": {}}
_lock = threading.Lock()


def reset_stats() -> None:
    with _lock:
        for k in STATS:
            STATS[k] = {} if k == "reasons" else 0


def _stat(key: str, reason: str | None = None) -> None:
    with _lock:
        STATS[key] += 1
        if reason:
            STATS["reasons"][reason] = STATS["reasons"].get(reason, 0) + 1


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", s.lower())


def validate_condensed(old: str, new: str, keep_terms: tuple[str, ...] = ()) -> str | None:
    """None if acceptable, else the reason it is rejected. keep_terms: glossary terms; each one the original
    contains must survive the shortening."""
    if not new.strip():
        return "empty"
    if any(match_en(t, old) and not match_en(t, new) for t in keep_terms):
        return "glossary term lost"
    if len(new) >= len(old):
        return "not shorter"
    if len(new) < MIN_KEEP * len(old):
        return "too short"
    if not set(re.findall(r"\d+", new)) <= set(re.findall(r"\d+", old)):
        return "new numbers"
    old_w, new_w = set(_words(old)), _words(new)
    if new_w and sum(w not in old_w for w in new_w) > max(MIN_NEW_ALLOWED, MAX_NEW_WORDS * len(new_w)):
        return "too many new words"
    return None


def same_meaning(original: str, shortened: str, llm: Callable[..., str] = chat) -> bool | None:
    """LLM judgement on the English side only: True (YES), False (any other answer), None (the LLM failed)."""
    try:
        reply = llm([{"role": "system", "content": JUDGE_PROMPT},
                     {"role": "user", "content": f"ORIGINAL: {original}\nSHORTENED: {shortened}"}])
    except Exception as e:
        log.warning("condense: meaning check failed (LLM error, not a NO): %s", e)
        return None
    words = re.findall(r"[A-Za-z]+", reply.upper())
    return bool(words) and words[0] == "YES"


def _model_name() -> str:
    """The configured LLM model. Read from LLM_MODEL even when the rest of the LLM setup is missing, so the cache
    key still tells models apart (found 2026-11-22: in a clean container every model was "unknown")."""
    try:
        return get_llm_config()["model"]
    except Exception:
        return {k.lower(): v for k, v in load_env().items()}.get("llm_model") or "unknown"


def _cache_file(cache_dir: Path | None, text: str, target: int, prev: str, nxt: str,
                terms: tuple[str, ...] = ()) -> Path | None:
    """The key includes the LLM model, so a run with another model never reuses this model's answers. Glossary
    terms join the key only when the sentence has some, so runs without a glossary keep their cache."""
    if cache_dir is None:
        return None
    parts = [_model_name(), SYSTEM_PROMPT, JUDGE_PROMPT, MAX_TRIES, text, target, prev, nxt]
    key = json.dumps(parts + [sorted(terms)] if terms else parts, ensure_ascii=False)
    return Path(cache_dir) / f"condense_{hashlib.sha256(key.encode()).hexdigest()[:24]}.json"


def condense(text: str, target_chars: int, prev: str = "", nxt: str = "",
             llm: Callable[..., str] = chat, cache_dir: str | Path | None = None,
             keep_terms: tuple[str, ...] = ()) -> str | None:
    """Return a shorter English text with the same meaning, or None (every candidate rejected, or LLM error).
    keep_terms: glossary terms; the ones this sentence contains are named in the prompt and must be kept."""
    _stat("calls")
    terms = tuple(t for t in keep_terms if match_en(t, text))
    cache = _cache_file(Path(cache_dir) if cache_dir else None, text, target_chars, prev, nxt, terms)
    if cache and cache.is_file():
        _stat("cached")
        return json.loads(cache.read_text(encoding="utf-8"))["result"]

    ctx = ""
    if terms:
        ctx += "Glossary terms (keep each one, word for word): " + ", ".join(terms) + "\n"
    if prev:
        ctx += f"Previous sentence (context only): {prev}\n"
    if nxt:
        ctx += f"Next sentence (context only): {nxt}\n"
    mild = max(target_chars, (len(text) + target_chars) // 2)
    feedback, result, reason = "", None, "no candidate"
    for attempt in range(1, MAX_TRIES + 1):
        goal = target_chars if attempt < MAX_TRIES else mild
        base = f"{ctx}\nTarget: at most {goal} characters (original is {len(text)}).\n\nSentence to shorten:\n{text}"
        if attempt == MAX_TRIES:
            feedback += ("\n\nThis time only remove filler words, adjectives and minor detail; it is fine to miss the "
                         "target. Keep every name, number and the main message.")
        try:
            reply = llm([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": base + feedback}])
        except Exception as e:
            log.warning("condense: LLM failed (not cached): %s", e)
            _stat("llm_errors", "llm error")
            return None
        new = reply.strip().strip('"').strip().splitlines()[0].strip() if reply.strip() else ""
        reason = validate_condensed(text, new, terms)
        if reason is None:
            ok = same_meaning(text, new, llm)
            if ok is None:
                _stat("llm_errors", "llm error")
                return None
            reason = None if ok else "meaning changed"
        if reason is None:
            log.info("condense: %r -> %r (attempt %d)", text, new, attempt)
            result = new
            break
        log.warning("condense: attempt %d rejected %r -> %r (%s)", attempt, text, new, reason)
        feedback = (f"\n\nYour previous answer was rejected ({reason}): {new}\n"
                    "Try again. Keep every important idea of the original.")
    _stat("accepted" if result else "rejected", None if result else reason)
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"result": result}, ensure_ascii=False), encoding="utf-8")
    return result
