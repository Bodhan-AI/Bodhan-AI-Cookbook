"""LLM review of sentence boundaries: merge fragments that were ended too early.

The LLM only decides WHICH consecutive sentences belong together and returns the joined English text.
Safety checks (a merge is rejected unless all pass):
  * ids are consecutive and in order,
  * letters/digits are unchanged (only case and punctuation may differ),
  * the token count equals the sum of the source tokens (so word times can be kept).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Callable

from .llm import chat
from .punctuate import words_unchanged

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You review sentence boundaries in an automatically punctuated English speech transcript.

Each line is: `<id>|gap=<seconds of silence before it>|<sentence>`.
Sometimes the punctuation step ended a sentence too early, leaving a fragment, and the next line continues it.
Example: `11|gap=0.8s|But in this case.` followed by `12|gap=0.8s|The bacterium could do something the host couldn't.`
should be one sentence: "But in this case, the bacterium could do something the host couldn't."

Task: find groups of consecutive lines that are really ONE sentence, and output one line per group:
MERGE <id>,<id>,...|<merged sentence>

Rules:
1. Merge ONLY when a line is clearly an incomplete fragment or the break is clearly wrong. Complete sentences stay separate, even if short. When unsure, do not merge.
2. The merged sentence must keep every word, in order. Do not add, remove, replace or correct any word. Only change punctuation at the join (use a comma or nothing instead of the full stop) and lowercase the first word of the continuation unless it is "I", a proper noun or an acronym.
3. Only merge ids listed under LINES TO REVIEW. CONTEXT lines are for reference.
4. Groups must be consecutive ids. Max 4 lines per group.
5. If nothing needs merging, output exactly: NONE
No other text."""

_MERGE = re.compile(r"^\s*MERGE\s+([\d,\s]+?)\s*\|\s*(.+?)\s*$")


def parse_merges(reply: str) -> list[tuple[list[int], str]]:
    out = []
    for line in reply.splitlines():
        m = _MERGE.match(line)
        if m:
            ids = [int(x) for x in m.group(1).split(",") if x.strip()]
            out.append((ids, m.group(2)))
    return out


def validate_merge(ids: list[int], merged: str, by_id: dict[int, dict]) -> str | None:
    """Return None if the merge is acceptable, else the reason it is rejected."""
    if len(ids) < 2 or len(ids) > 4:
        return "group size"
    if any(i not in by_id for i in ids):
        return "unknown id"
    if ids != list(range(ids[0], ids[0] + len(ids))):
        return "not consecutive"
    src = [by_id[i] for i in ids]
    if not words_unchanged(" ".join(s["text"] for s in src), merged):
        return "words changed"
    if len(merged.split()) != sum(len(s["words"]) for s in src):
        return "token count differs"
    return None


def _apply_group(group: list[dict], merged: str) -> dict:
    words = [w for s in group for w in s["words"]]
    toks = merged.split()
    words = [{**w, "t": t} for w, t in zip(words, toks)]
    start_exact, end_exact = group[0]["start_exact"], group[-1]["end_exact"]
    return {
        "words": words, "text": merged,
        "start": group[0]["start"], "end": group[-1]["end"],
        "start_exact": start_exact, "end_exact": end_exact,
        "chunk_ids": sorted({c for s in group for c in s["chunk_ids"]}),
        "timing": "exact" if start_exact and end_exact else "estimated",
        "merged_from": [s["id"] for s in group],
    }


def review_boundaries(
    sentences: list[dict],
    work_dir: str | Path,
    llm: Callable[..., str] = chat,
    model: str | None = None,
    window: int = 40,
    context: int = 3,
) -> tuple[list[dict], list[dict]]:
    """Returns (new_sentences, log) where log lists every proposed merge and whether it was applied."""
    cache_dir = Path(work_dir) / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    by_id = {s["id"]: s for s in sentences}

    def fmt(s: dict, i: int) -> str:
        gap = s["start"] - sentences[i - 1]["end"] if i > 0 else 0.0
        return f"{s['id']}|gap={max(gap, 0):.1f}s|{s['text']}"

    proposals: list[tuple[list[int], str]] = []
    for a in range(0, len(sentences), window):
        b = min(a + window, len(sentences))
        parts = []
        if a > 0:
            parts.append("CONTEXT (before):\n" + "\n".join(fmt(sentences[i], i) for i in range(max(0, a - context), a)))
        parts.append("LINES TO REVIEW:\n" + "\n".join(fmt(sentences[i], i) for i in range(a, b)))
        if b < len(sentences):
            parts.append("CONTEXT (after):\n" + "\n".join(fmt(sentences[i], i) for i in range(b, min(len(sentences), b + context))))
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "\n\n".join(parts)}]
        key = hashlib.sha256((json.dumps(msgs) + str(model)).encode()).hexdigest()[:24]
        cache = cache_dir / f"review_{key}.json"
        if cache.is_file():
            reply = json.loads(cache.read_text(encoding="utf-8"))["reply"]
        else:
            try:
                reply = llm(msgs, model=model) if model else llm(msgs)
            except Exception as e:
                log.warning("review: LLM failed for window %d-%d: %s (no merges there)", a, b, e)
                continue
            cache.write_text(json.dumps({"reply": reply}, ensure_ascii=False), encoding="utf-8")
        window_ids = {s["id"] for s in sentences[a:b]}
        for ids, merged in parse_merges(reply):
            if ids and ids[0] in window_ids:
                proposals.append((ids, merged))

    used: set[int] = set()
    applied: dict[int, tuple[list[int], str]] = {}
    audit = []
    for ids, merged in proposals:
        reason = validate_merge(ids, merged, by_id)
        if reason is None and used & set(ids):
            reason = "overlaps another merge"
        audit.append({"ids": ids, "merged": merged, "applied": reason is None, "rejected_because": reason})
        if reason is None:
            used |= set(ids)
            applied[ids[0]] = (ids, merged)
        else:
            log.warning("review: rejected merge %s (%s)", ids, reason)

    out: list[dict] = []
    skip: set[int] = set()
    for s in sentences:
        if s["id"] in skip:
            continue
        if s["id"] in applied:
            ids, merged = applied[s["id"]]
            skip |= set(ids)
            out.append(_apply_group([by_id[i] for i in ids], merged))
        else:
            out.append(dict(s))
    for n, s in enumerate(out, 1):
        s["id"] = n
    log.info("review: %d sentences -> %d (%d merges applied, %d rejected)", len(sentences), len(out),
             len(applied), sum(not a["applied"] for a in audit))
    return out, audit


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    work = Path(sys.argv[1])
    model = sys.argv[2] if len(sys.argv) > 2 else None
    sents = json.loads((work / "sentences.json").read_text(encoding="utf-8"))
    new, audit = review_boundaries(sents, work, model=model)
    (work / "sentences_reviewed.json").write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    (work / "review_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
