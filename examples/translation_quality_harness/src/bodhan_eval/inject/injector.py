"""Turn clean items into labelled test cases, deterministically (seeded per item).

Two routes, both allowed by the brief:
- programmatic edits of the existing translation (text_ops) and audio (audio_ops);
- perturbing the English source and re-translating with indic-translate (`retranslate_*`).
The LLM is used only to *point at* the translation tokens that render a source name (indices,
never text), so the term op knows which token to corrupt.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import random
from collections import Counter

import regex

from ..judge.rubric import numbered
from ..languages import get_language
from ..text.segment import clauses, tokens, words
from . import text_ops

TRANSLATION_OPS = list(text_ops.OPS)
ANTONYMS = [
    ("increase", "decrease"),
    ("increases", "decreases"),
    ("before", "after"),
    ("hot", "cold"),
    ("north", "south"),
    ("east", "west"),
    ("always", "never"),
    ("more", "less"),
    ("higher", "lower"),
    ("large", "small"),
    ("larger", "smaller"),
    ("first", "last"),
    ("open", "closed"),
    ("can", "cannot"),
    ("rise", "fall"),
    ("win", "lose"),
    ("won", "lost"),
    ("above", "below"),
    ("inside", "outside"),
    ("early", "late"),
    ("fast", "slow"),
    ("strong", "weak"),
    ("old", "new"),
    ("same", "different"),
    ("accept", "reject"),
    ("allowed", "banned"),
]


def item_rng(seed: int, item_id: str, lang: str) -> random.Random:
    digest = hashlib.sha256(f"{seed}:{item_id}:{lang}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def _order(rows: list[dict], seed: int) -> list[int]:
    return sorted(range(len(rows)), key=lambda i: hashlib.sha256(f"{seed}:{rows[i]['id']}:{rows[i]['lang']}".encode()).hexdigest())


def inject_text(
    rows: list[dict],
    *,
    rate: float = 0.5,
    seed: int = 13,
    categories: list[str] | None = None,
    term_spans: dict[tuple[str, str], tuple[int, int]] | None = None,
) -> list[dict]:
    """Return new rows: about `rate` of them carry exactly one injected translation error, the rest are clean (labels: [])."""
    cats = categories or TRANSLATION_OPS
    out = [dict(r) for r in rows]
    by_lang: dict[str, list[str]] = {}
    for r in rows:
        by_lang.setdefault(r["lang"], []).append(r.get("translation") or "")
    target = round(len(rows) * rate)
    used: Counter = Counter()
    injected = 0
    for idx in _order(rows, seed):
        row = out[idx]
        row.setdefault("labels", [])
        if injected >= target or not row.get("translation"):
            continue
        rng = item_rng(seed, str(row["id"]), row["lang"])
        script = get_language(row["lang"]).script
        pool = [t for t in by_lang[row["lang"]] if t and t != row["translation"]]
        options = sorted(cats, key=lambda c: (used[c], rng.random()))
        for cat in options:
            if not text_ops.applicable(cat, row["source"], row["translation"]):
                continue
            kwargs = {
                "pool": pool,
                "lang_script": script,
                "alternates": row.get("alternates"),
                "term_span": (term_spans or {}).get((str(row["id"]), row["lang"])),
            }
            if cat == "term" and kwargs["term_span"] is None and not regex.search(r"[A-Za-z]{3,}", row["translation"]):
                continue
            result = text_ops.OPS[cat](row["source"], row["translation"], rng, **kwargs)
            if result is None:
                continue
            row["translation"] = result.translation
            row["labels"] = [result.label]
            row.setdefault("meta", {})["injection"] = result.note
            used[cat] += 1
            injected += 1
            break
    for row in out:
        row.pop("alternates", None)
    return out


async def align_terms(rows: list[dict], llm) -> dict[tuple[str, str], tuple[int, int]]:
    """Ask the LLM which translation tokens render a source name. It answers with indices only."""
    from ..checks.roundtrip import source_entities

    async def one(row: dict):
        ents = [e for e in source_entities(row["source"]) if len(e.text) >= 3]
        translation = row.get("translation") or ""
        if not ents or not translation:
            return None
        toks = tokens(translation)
        prompt = (
            "Which translation tokens render the English name or term below? Answer with token indices only; never write target-language text.\n"
            f'NAME: "{ents[0].text}"\nTRANSLATION (numbered tokens): {numbered([t.text for t in toks])}\n'
            'Reply with JSON {"tokens": [start, end]} (inclusive) or {"tokens": null} if the name is not rendered.'
        )
        try:
            raw, _ = await llm.complete_json([{"role": "user", "content": prompt}], namespace="inject:align")
        except Exception:  # noqa: BLE001 - alignment is best effort
            return None
        rng = raw.get("tokens") if isinstance(raw, dict) else None
        if isinstance(rng, list) and len(rng) == 2 and all(isinstance(v, int) for v in rng):
            a, b = sorted(rng)
            if 0 <= a < len(toks) and b < len(toks) and b - a <= 3:
                return (str(row["id"]), row["lang"]), (toks[a].start, toks[b].end)
        return None

    found = await asyncio.gather(*(one(r) for r in rows))
    return dict(f for f in found if f)


def perturb_source(source: str, op: str, rng: random.Random, donors: list[str]) -> tuple[str, dict] | None:
    """English-side perturbation for the re-translation route. Returns (perturbed source, info)."""
    if op == "omission":
        cl = [c for c in clauses(source) if len(words(c.text)) >= 3]
        if len(cl) < 2:
            return None
        c = rng.choice(cl[1:])
        return (source[: c.start] + source[c.end :]).replace("  ", " ").strip(), {"source_span": [c.start, c.end]}
    if op == "addition":
        extra = [d for d in donors if 20 <= len(d) <= 140]
        if not extra:
            return None
        return source.rstrip() + " " + rng.choice(extra), {}
    if op == "mistranslation":
        for a, b in rng.sample(ANTONYMS, len(ANTONYMS)):
            for x, y in ((a, b), (b, a)):
                m = regex.search(rf"\b{x}\b", source)
                if m:
                    return source[: m.start()] + y + source[m.end() :], {"swap": [x, y]}
        return None
    return None


def diff_span(old: str, new: str) -> tuple[int, int] | None:
    """Character span in `new` covering what changed relative to `old` (word granularity)."""
    a, b = [w.text for w in words(old)], words(new)
    sm = difflib.SequenceMatcher(a=a, b=[w.text for w in b], autojunk=False)
    changed = [(j1, j2) for op, _, _, j1, j2 in sm.get_opcodes() if op in {"replace", "insert"} and j2 > j1]
    if not changed:
        return None
    j1, j2 = changed[0][0], changed[-1][1]
    return (b[j1].start, b[j2 - 1].end)


async def retranslate(row: dict, op: str, client, rng: random.Random, donors: list[str]) -> dict | None:
    perturbed = perturb_source(row["source"], op, rng, donors)
    if perturbed is None:
        return None
    new_source, info = perturbed
    result = await client.translate(new_source, row["lang"])
    new_translation = result.value["translation"]
    if op == "omission":
        label = {"type": "omission", "severity": "major", "span": info["source_span"], "field": "source", "annotator": "injector"}
    else:
        span = diff_span(row["translation"], new_translation)
        if span is None:
            return None
        label = {"type": op, "severity": "major", "span": list(span), "field": "translation", "annotator": "injector"}
    return {**row, "translation": new_translation, "labels": [label], "meta": {**row.get("meta", {}), "injection": f"re-translated a perturbed source ({op})"}}
