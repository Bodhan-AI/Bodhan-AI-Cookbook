"""Turn punctuated chunks into sentences with start/end times taken from REAL speech regions.

The ASR gives no word timestamps, so timing comes from the audio:
  * each chunk carries its VAD speech sub-regions (pauses between them are real);
  * every word is assigned to one region (by its position on the speech-time axis, i.e. pauses excluded),
    and spread inside that region, so a region's first word starts exactly at the region start;
  * if the chunk has alignment ANCHORS (align.py: pieces between pauses transcribed on their own), the words
    before each anchored pause stay in the regions before it, so a sentence break there is exact;
  * any other sentence boundary inside a chunk is SNAPPED to the nearest real pause (if one is close enough),
    so the next sentence starts exactly where the speaker starts again.
A boundary with no pause nearby stays "estimated".
"""

from __future__ import annotations

import bisect
import json
import re
from pathlib import Path

ABBREVIATIONS = {"mr.", "mrs.", "ms.", "dr.", "prof.", "vs.", "etc.", "e.g.", "i.e.", "st.", "no."}
_END = re.compile(r"[.?!]+[\"')\]]*$")

SNAP_TOL_MIN = 0.6    # seconds (on the speech-time axis) a boundary may move to reach a pause
SNAP_TOL_FRAC = 0.15  # ... or this share of the chunk's speech time, whichever is larger


def is_sentence_end(token: str) -> bool:
    return bool(_END.search(token)) and token.lower().rstrip("\"')]") not in ABBREVIATIONS


def chunk_regions(c: dict) -> list[tuple[float, float]]:
    regs = [(float(a), float(b)) for a, b in (c.get("regions") or []) if b > a]
    if regs:
        return sorted(regs)
    c0 = c["vad_start"] if c.get("vad_start") is not None else c["start"]
    c1 = c["vad_end"] if c.get("vad_end") is not None else c["end"]
    return [(c0, c1)]


def _weights(tokens: list[str]) -> list[int]:
    return [len(t) + 1 for t in tokens]


def _cum_ends(regs: list[tuple[float, float]]) -> list[float]:
    out, acc = [], 0.0
    for a, b in regs:
        acc += b - a
        out.append(acc)
    return out


def snap_boundaries(tokens: list[str], regs: list[tuple[float, float]]) -> dict[int, int]:
    """{token index k: region index j}: sentence ending at token k is snapped to the pause after region j."""
    if len(regs) < 2:
        return {}
    w = _weights(tokens)
    total_w, cum = sum(w), _cum_ends(regs)
    speech = cum[-1]
    tol = max(SNAP_TOL_MIN, SNAP_TOL_FRAC * speech)
    chosen: dict[int, int] = {}
    last_j, acc = -1, 0
    for k, t in enumerate(tokens[:-1]):
        acc += w[k]
        if not is_sentence_end(t):
            continue
        u = acc / total_w * speech
        cands = range(last_j + 1, len(regs) - 1)
        if not cands:
            break
        j = min(cands, key=lambda j: abs(cum[j] - u))
        if abs(cum[j] - u) <= tol:
            chosen[k], last_j = j, j
    return chosen


def place_words(tokens: list[str], regs: list[tuple[float, float]]) -> list[dict]:
    """Times for tokens spoken within regs. Each token goes to one region; region edges are exact."""
    if not tokens:
        return []
    if len(tokens) == 1:
        return [{"tok": tokens[0], "start": regs[0][0], "end": regs[-1][1], "xs": True, "xe": True}]
    w = _weights(tokens)
    total_w, cum = sum(w), _cum_ends(regs)
    speech = cum[-1]
    idx, acc = [], 0.0
    for k in range(len(tokens)):
        mid = (acc + w[k] / 2) / total_w * speech
        acc += w[k]
        idx.append(min(bisect.bisect_left(cum, mid), len(regs) - 1))
    idx[0], idx[-1] = 0, len(regs) - 1          # speech starts with the first word, ends with the last
    for k in range(1, len(idx)):                  # keep monotonic
        idx[k] = max(idx[k], idx[k - 1])
    words = []
    for j, (a, b) in enumerate(regs):
        ks = [k for k in range(len(tokens)) if idx[k] == j]
        if not ks:
            continue
        tot, pos = sum(w[k] for k in ks), 0
        for n, k in enumerate(ks):
            s = a + (b - a) * pos / tot
            pos += w[k]
            words.append({"tok": tokens[k], "start": s, "end": a + (b - a) * pos / tot,
                          "xs": n == 0, "xe": n == len(ks) - 1})
    return words


def anchor_segments(n_tok: int, n_reg: int, anchors: list | None) -> list[tuple[int, int, int, int]]:
    """Split a chunk at its alignment anchors (align.py): [(t0, t1, r0, r1)] token and region ranges.
    Anchor (k, j) means tokens[:k] are spoken before the pause after region j. Invalid anchors are ignored."""
    segs, t0, r0 = [], 0, 0
    for k, j in sorted(anchors or []):
        if t0 < k < n_tok and r0 <= j < n_reg - 1:
            segs.append((t0, k, r0, j + 1))
            t0, r0 = k, j + 1
    segs.append((t0, n_tok, r0, n_reg))
    return segs


def chunk_words(c: dict, stats: dict | None = None) -> list[dict]:
    """Words of one chunk with times. Words stay inside the regions their anchors give them (if any); inside
    each anchored part, sentence boundaries are snapped to the nearest pause as before.
    stats (optional) gets counts: internal_boundaries, snapped_to_pause, at_aligned_pause."""
    tokens = c.get("text", "").split()
    if not tokens:
        return []
    regs = chunk_regions(c)
    words, snapped, aligned = [], 0, 0
    for t0, t1, r0, r1 in anchor_segments(len(tokens), len(regs), c.get("anchors")):
        tk, rg = tokens[t0:t1], regs[r0:r1]
        chosen = snap_boundaries(tk, rg)
        ta, ra = 0, 0
        for k in sorted(chosen) + [len(tk) - 1]:
            rb = chosen.get(k, len(rg) - 1)
            words += place_words(tk[ta : k + 1], rg[ra : rb + 1])
            ta, ra = k + 1, rb + 1
        snapped += len(chosen)
        if t1 < len(tokens) and is_sentence_end(tokens[t1 - 1]):
            aligned += 1
    for wd in words:
        wd["chunk"] = c["id"]
    if stats is not None:
        stats["internal_boundaries"] = stats.get("internal_boundaries", 0) + sum(map(is_sentence_end, tokens[:-1]))
        stats["snapped_to_pause"] = stats.get("snapped_to_pause", 0) + snapped + aligned
        stats["at_aligned_pause"] = stats.get("at_aligned_pause", 0) + aligned
    return words


def build_sentences(chunks: list[dict]) -> list[dict]:
    """chunks: dicts with id, start, end, text (punctuated), optional vad_start/vad_end/regions."""
    words: list[dict] = []
    for c in chunks:
        words += chunk_words(c)

    sentences, cur = [], []

    def flush():
        if not cur:
            return
        start_exact, end_exact = cur[0]["xs"], cur[-1]["xe"]
        sentences.append({
            "id": len(sentences) + 1,
            "text": " ".join(w["tok"] for w in cur),
            "start": round(cur[0]["start"], 3), "end": round(cur[-1]["end"], 3),
            "start_exact": start_exact, "end_exact": end_exact,
            "timing": "exact" if start_exact and end_exact else "estimated",
            "chunk_ids": sorted({w["chunk"] for w in cur}),
            "words": [{"t": w["tok"], "s": round(w["start"], 3), "e": round(w["end"], 3), "xs": w["xs"], "xe": w["xe"]}
                      for w in cur],
        })
        cur.clear()

    for w in words:
        cur.append(w)
        if is_sentence_end(w["tok"]):
            flush()
    flush()
    return sentences


def snap_stats(chunks: list[dict]) -> dict:
    stats = {"internal_boundaries": 0, "snapped_to_pause": 0, "at_aligned_pause": 0}
    for c in chunks:
        chunk_words(c, stats)
    return stats


if __name__ == "__main__":
    import sys

    work = Path(sys.argv[1])
    chunks = json.loads((work / "transcript_punct.json").read_text(encoding="utf-8"))
    sents = build_sentences(chunks)
    (work / "sentences.json").write_text(json.dumps(sents, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(sents)} sentences | exact {sum(s['timing'] == 'exact' for s in sents)} | {snap_stats(chunks)}")
