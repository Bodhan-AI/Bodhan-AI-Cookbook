"""Pause-level alignment: find which words are spoken between which real pauses.

Indic-Transcribe returns no word times. Without help, a chunk's words are spread over its speech regions by
character count, so a sentence break can land on the wrong pause. Measured on maths_variant: "Write the
definition..." was placed 2.2 s early, because the previous sentence ran into one more region than its
length suggested.

Fix: cut each multi-region chunk at its pauses into pieces, transcribe every piece on its own, and align the
pieces' letters to the full-chunk transcript. Where piece p's letters end and piece p+1's begin, the transcript
gets an ANCHOR (k, j): tokens[:k] are spoken before the pause that follows region j.

  * The text always stays the full-chunk transcript; pieces only decide timing.
  * Alignment is on letters, not words, so edge differences ("Write"/"Right", "collinear"/"Collinea") still
    align.
  * An anchor is kept only when it is clear: both pieces next to the pause matched the transcript, and one word
    boundary clearly fits the place where they meet. Anything unclear keeps the old estimate
    (sentences.snap_boundaries), so this can only add certainty, never remove text.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

import soundfile as sf

from .chunking import Chunk
from .config import get_key
from .punctuate import norm_words
from .transcribe import transcribe_file

log = logging.getLogger(__name__)

MIN_REGION = 0.3   # s of speech; a shorter region joins a neighbour (shortest tested that transcribed right: 0.32 s)
MIN_BLOCK = 3      # letters; shorter matches between transcripts happen by chance and are ignored
MIN_MATCH = 0.6    # share of the pieces' letters that must match the transcript, else the chunk gets no anchors.
                   # Measured on the pieces' letters, not the chunk's: under loud music some pieces come back as
                   # junk with no Latin letters at all (seen: a long run of one Oriya sign); such a piece simply
                   # gives no anchors, and the clean pieces around it still count.
MAX_SHIFT = 3      # letters; how far an unclear meeting point may be from the word boundary it is snapped to


# --------------------------------------------------------------------------- pieces
def build_pieces(regions: list, start: float, end: float, min_region: float = MIN_REGION) -> list[dict]:
    """Group a chunk's speech regions into pieces cut at pauses. Returns [{r0, r1, t0, t1}] (region index range,
    inclusive, and the absolute time window). Windows run from mid-pause to mid-pause, so they tile the chunk."""
    regs = [(float(a), float(b)) for a, b in regions]
    groups = [[j] for j in range(len(regs))]

    def speech(g):
        return sum(regs[j][1] - regs[j][0] for j in g)

    def gap(left, right):  # pause between two adjacent groups
        return regs[right[0]][0] - regs[left[-1]][1]

    while len(groups) > 1:
        i = min(range(len(groups)), key=lambda n: speech(groups[n]))
        if speech(groups[i]) >= min_region:
            break
        if i == 0:
            n = 1
        elif i == len(groups) - 1:
            n = i - 1
        else:   # merge across the shorter pause, so the longer one stays a cut
            n = i - 1 if gap(groups[i - 1], groups[i]) <= gap(groups[i], groups[i + 1]) else i + 1
        a, b = sorted((i, n))
        groups[a:b + 1] = [groups[a] + groups[b]]

    pieces = []
    for n, g in enumerate(groups):
        r0, r1 = g[0], g[-1]
        t0 = start if n == 0 else (regs[r0 - 1][1] + regs[r0][0]) / 2
        t1 = end if n == len(groups) - 1 else (regs[r1][1] + regs[r1 + 1][0]) / 2
        pieces.append({"r0": r0, "r1": r1, "t0": round(t0, 3), "t1": round(t1, 3)})
    return pieces


# --------------------------------------------------------------------------- anchors (pure)
def _letters(text: str) -> str:
    return "".join(norm_words(text))


def find_anchors(tokens: list[str], piece_texts: list[str | None], piece_last_region: list[int]) -> dict:
    """Align piece transcripts to the chunk tokens.

    piece_texts[p] is piece p's transcript (None if it failed); piece_last_region[p] is the index of its last
    region. Returns {"anchors": [[k, j], ...], "match": share of the pieces' letters matched, "dropped": {...}}.
    """
    n_pause = len(piece_texts) - 1
    out = {"anchors": [], "match": 0.0, "dropped": {}}

    def drop(reason: str, n: int = 1) -> None:
        out["dropped"][reason] = out["dropped"].get(reason, 0) + n

    tok_letters = [_letters(t) for t in tokens]
    C = "".join(tok_letters)
    if not C or n_pause < 1:
        return out
    starts, acc = [], 0
    for s in tok_letters:
        starts.append(acc)
        acc += len(s)
    bounds = sorted({starts[k] for k in range(1, len(tokens)) if 0 < starts[k] < len(C)})
    tok_at = {}
    for k in range(1, len(tokens)):            # first token starting at each letter offset (skips empty tokens)
        tok_at.setdefault(starts[k], k)

    pl = [_letters(t or "") for t in piece_texts]
    P = "".join(pl)
    pb, acc = [], 0
    for s in pl:
        pb.append(acc)
        acc += len(s)
    pb.append(acc)

    blocks = [b for b in SequenceMatcher(None, C, P, autojunk=False).get_matching_blocks() if b.size >= MIN_BLOCK]
    out["match"] = round(sum(b.size for b in blocks) / len(P), 3) if P else 0.0
    if out["match"] < MIN_MATCH:
        drop("low_match", n_pause)
        return out
    pmap = [-1] * len(P)
    for b in blocks:
        for d in range(b.size):
            pmap[b.b + d] = b.a + d

    def matched(p: int) -> bool:
        return piece_texts[p] is not None and any(pmap[i] >= 0 for i in range(pb[p], pb[p + 1]))

    found = []
    for p in range(n_pause):
        if not (matched(p) and matched(p + 1)):
            drop("unmatched_piece")
            continue
        x = pb[p + 1]                                   # where piece p+1 starts in P
        i_before = next(i for i in range(x - 1, -1, -1) if pmap[i] >= 0)
        i_after = next(i for i in range(x, len(P)) if pmap[i] >= 0)
        lo, hi = pmap[i_before] + 1, pmap[i_after]      # the pause lies between these letters of C
        if hi < lo:
            drop("crossing")
            continue
        u_left, u_right = x - i_before - 1, i_after - x  # unmatched letters each piece has at the meeting point
        if (hi - lo) - (u_left + u_right) > MAX_SHIFT:   # the full transcript has words neither piece heard
            drop("unheard_words")
            continue
        est = lo if u_left + u_right == 0 else lo + (hi - lo) * u_left / (u_left + u_right)
        near = sorted(bounds, key=lambda b: abs(b - est))
        if not near:
            drop("no_word_boundary")
            continue
        d0 = abs(near[0] - est)
        d1 = abs(near[1] - est) if len(near) > 1 else float("inf")
        if d0 > MAX_SHIFT:
            drop("inside_word")
        elif d0 > 0 and d1 - d0 < 2:
            drop("ambiguous")
        else:
            found.append([tok_at[near[0]], piece_last_region[p]])

    # anchors must move forward in both tokens and regions; a repeat means the alignment is not trustworthy there
    ks = [k for k, _ in found]
    for a in found:
        if ks.count(a[0]) > 1:
            drop("duplicate")
        else:
            out["anchors"].append(a)
    out["anchors"].sort()
    return out


# --------------------------------------------------------------------------- driver
def align_chunks(
    punct: list[dict],
    chunks: list[Chunk],
    work_dir: str | Path,
    lang: str = "en",
    concurrency: int = 4,
    transcribe: Callable = transcribe_file,
) -> tuple[list[dict], dict]:
    """Add `anchors` to every punctuated chunk that has 2+ speech regions. Returns (chunks, stats).

    Piece audio is cut from each chunk's ASR file (the same audio the full-chunk ASR heard). Results are cached
    like any ASR call, and an audit is written to <work_dir>/alignment.json.
    """
    work_dir = Path(work_dir)
    piece_dir, cache_dir = work_dir / "align", work_dir / "cache"
    piece_dir.mkdir(parents=True, exist_ok=True)
    by_id = {c.id: c for c in chunks}
    key = get_key("transcribe")

    jobs = []  # (chunk dict, pieces, [piece wav paths])
    for c in punct:
        ch = by_id.get(c["id"])
        regs = c.get("regions") or []
        if not (c.get("ok", True) and c.get("text", "").strip() and ch and ch.path and len(regs) >= 2):
            continue
        pieces = build_pieces(regs, c["start"], c["end"])
        if len(pieces) < 2:
            continue
        wav, sr = sf.read(ch.path, dtype="float32")
        paths = []
        for n, pc in enumerate(pieces):
            a, b = int(round((pc["t0"] - c["start"]) * sr)), int(round((pc["t1"] - c["start"]) * sr))
            p = piece_dir / f"chunk_{c['id']:04d}_p{n:02d}.wav"
            sf.write(str(p), wav[max(0, a):b], sr, subtype="PCM_16")
            paths.append(p)
        jobs.append((c, pieces, paths))

    flat = [p for _, _, paths in jobs for p in paths]
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        replies = dict(zip(flat, ex.map(lambda p: transcribe(p, lang, key, cache_dir), flat)))

    anchors_by_id, audit = {}, []
    stats = {"chunks": len(jobs), "pieces": len(flat), "pauses": 0, "anchors": 0, "dropped": {},
             "failed_pieces": 0, "billed_sec": 0.0}
    for c, pieces, paths in jobs:
        texts = []
        for pc, p in zip(pieces, paths):
            r = replies[p]
            usable = r.get("ok") and not r.get("junk")   # a junk reply holds only part of the piece's words
            texts.append(r["text"] if usable else None)
            stats["failed_pieces"] += not usable
            if r.get("ok") and not r.get("cached"):
                stats["billed_sec"] += (pc["t1"] - pc["t0"]) * r.get("calls", 1)
        tokens = c["text"].split()
        res = find_anchors(tokens, texts, [pc["r1"] for pc in pieces])
        anchors_by_id[c["id"]] = res["anchors"]
        stats["pauses"] += len(pieces) - 1
        stats["anchors"] += len(res["anchors"])
        for k, v in res["dropped"].items():
            stats["dropped"][k] = stats["dropped"].get(k, 0) + v
        audit.append({"id": c["id"], "match": res["match"], "dropped": res["dropped"],
                      "pieces": [{**pc, "text": t} for pc, t in zip(pieces, texts)],
                      "anchors": [{"token": k, "after_region": j, "pause_end": c["regions"][j + 1][0],
                                   "next_words": " ".join(tokens[k:k + 4])} for k, j in res["anchors"]]})
    stats["billed_sec"] = round(stats["billed_sec"], 1)
    (work_dir / "alignment.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("align: %d chunks, %d pieces, %d/%d pauses anchored, dropped=%s, failed pieces=%d, billed %.1fs",
             stats["chunks"], stats["pieces"], stats["anchors"], stats["pauses"], stats["dropped"],
             stats["failed_pieces"], stats["billed_sec"])
    return [{**c, "anchors": anchors_by_id.get(c["id"], [])} for c in punct], stats
