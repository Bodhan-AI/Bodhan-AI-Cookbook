"""Split sentences into dubbing units. Plain deterministic code, no LLM.

Shorter units anchored on real pauses keep the dub in sync (both ends of every unit line up with the
speaker), so a sentence is split:
  * always, if it is longer than MAX_SEC (cut near the middle, preferring real pauses);
  * also at a REAL pause >= PAUSE_SPLIT seconds that sits on a clause boundary (, ; : or a conjunction),
    if both parts are at least MIN_PART seconds long.
Cuts never happen mid-clause, so each unit still translates as a sensible phrase.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

MAX_SEC = 12.0
MIN_PART = 2.0
PAUSE_SPLIT = 0.5
CONJUNCTIONS = {"and", "but", "which", "so", "because", "while", "where", "when", "that", "or"}
CLAUSE_END = (",", ";", ":")


def _candidates(words: list[dict]) -> list[tuple[int, str, float]]:
    """Cut positions k (between words[k-1] and words[k]) with kind and the real pause length there."""
    out = []
    for k in range(1, len(words)):
        gap = max(0.0, words[k]["s"] - words[k - 1]["e"])
        if words[k - 1]["t"].endswith(CLAUSE_END):
            out.append((k, "clause", gap))
        elif words[k]["t"].lower().strip(",") in CONJUNCTIONS:
            out.append((k, "conjunction", gap))
    return out


def _split(words: list[dict], max_sec: float, min_part: float, pause_split: float, reasons: list[str]) -> list[list[dict]]:
    if len(words) < 2:
        return [words]
    start, end = words[0]["s"], words[-1]["e"]
    cands = _candidates(words)

    def ok(k: int, floor: float) -> bool:
        cut = words[k - 1]["e"]
        return cut - start >= floor and end - words[k]["s"] >= floor

    def recurse(k: int, why: str) -> list[list[dict]]:
        reasons.append(why)
        return _split(words[:k], max_sec, min_part, pause_split, reasons) + \
            _split(words[k:], max_sec, min_part, pause_split, reasons)

    if end - start > max_sec:
        mid = (start + end) / 2
        for kinds, floor in (({"clause"}, min_part), ({"clause", "conjunction"}, min_part),
                             ({"clause", "conjunction"}, 1.0)):
            best = [(abs(words[k - 1]["e"] - mid) - 5 * gap, k, kind) for k, kind, gap in cands
                    if kind in kinds and ok(k, floor)]
            if best:
                _, k, kind = min(best)
                return recurse(k, kind)
        reasons.append("too_long_no_split")
        return [words]

    pauses = [(gap, k) for k, kind, gap in cands if gap >= pause_split and ok(k, min_part)]
    if pauses:
        return recurse(max(pauses)[1], "pause")
    return [words]


def split_sentences(sentences: list[dict], max_sec: float = MAX_SEC, min_part: float = MIN_PART,
                    pause_split: float = PAUSE_SPLIT) -> list[dict]:
    """Return dubbing units: {id, sentence_id, text, start, end, timing, split, split_reasons, inner_pause}."""
    units: list[dict] = []
    for s in sentences:
        reasons: list[str] = []
        parts = _split(s["words"], max_sec, min_part, pause_split, reasons)
        for p in parts:
            xs, xe = p[0].get("xs", False), p[-1].get("xe", False)
            inner = sum(max(0.0, b["s"] - a["e"]) for a, b in zip(p, p[1:]))
            units.append({
                "id": len(units) + 1, "sentence_id": s["id"],
                "text": " ".join(w["t"] for w in p),
                "start": round(p[0]["s"], 3), "end": round(p[-1]["e"], 3),
                "timing": "exact" if xs and xe else "estimated",
                "split": "none" if len(parts) == 1 else "split",
                "split_reasons": reasons,
                "inner_pause": round(inner, 3),
            })
    by_sentence = {u["sentence_id"]: u["split_reasons"] for u in units}
    log.info("units: %d sentences -> %d units (%d cuts at pauses, %d too long to split)", len(sentences), len(units),
             sum(r.count("pause") for r in by_sentence.values()),
             sum("too_long_no_split" in r for r in by_sentence.values()))
    return units


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    work = Path(sys.argv[1])
    sents = json.loads((work / "sentences_reviewed.json").read_text(encoding="utf-8"))
    us = split_sentences(sents)
    (work / "units.json").write_text(json.dumps(us, ensure_ascii=False, indent=2), encoding="utf-8")
