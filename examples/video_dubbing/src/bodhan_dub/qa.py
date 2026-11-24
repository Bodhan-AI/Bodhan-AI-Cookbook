"""Back-transcription QA: detect repeated or missing speech in synthesized clips.

TTS reads digits and Latin words (ATP, 2019) as spoken words, and the ASR writes them as words, so such
reference tokens are WILDCARDS: they may match up to WILDCARD_SLOTS hyp words at no cost.
Error rate is measured in characters; inserted chars ~ repeats, deleted chars ~ omissions.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .transcribe import transcribe_file
from .translate import has_repetition

WILDCARD_SLOTS = 6
CER_MAX = 0.25        # provisional, to be tuned from data
INS_MAX = 0.15
DEL_MAX = 0.15


# A Latin word or number with a hyphen-attached native ending ("model-ஐ", "server-இல்", "ATP-यों") is spoken as
# one word, and the ASR writes it as one native word (மாடலை). Joined, it is one wildcard; split, the ending
# would count as missing (live code-mixed Tamil test, 2026-11-20: a false omission flag, CER 0.154 -> 0).
_LATIN_HYPHEN = re.compile(r"(?<=[A-Za-z0-9])[-‐‑](?=\w)|(?<=\w)[-‐‑](?=[A-Za-z0-9])")


def tokens(text: str) -> list[str]:
    """Lowercased words; punctuation and symbols (incl. the danda) are removed, marks are kept."""
    t = unicodedata.normalize("NFC", _LATIN_HYPHEN.sub("", text.lower()))
    t = "".join(" " if unicodedata.category(c)[0] in "PS" else c for c in t)
    return t.split()


def is_masked(tok: str) -> bool:
    return any((c.isascii() and c.isalnum()) or unicodedata.category(c) == "Nd" for c in tok)


def _lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def masked_error(ref: str, hyp: str) -> dict:
    """Character error rate with wildcards. Returns {cer, sub, ins, dele, ref_chars, ins_ratio, del_ratio}."""
    r_tok = tokens(ref)
    h = tokens(hyp)
    r: list[str | None] = []  # None = wildcard slot
    for t in r_tok:
        r.extend([None] * WILDCARD_SLOTS if is_masked(t) else [t])
    ref_chars = sum(len(t) for t in r_tok if not is_masked(t))

    n, m = len(r), len(h)
    INF = float("inf")
    cost = [[INF] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    cost[0][0] = 0
    for i in range(n + 1):
        for j in range(m + 1):
            c = cost[i][j]
            if c == INF:
                continue
            if i < n:  # delete ref token (free for wildcard slot)
                d = 0 if r[i] is None else len(r[i])
                if c + d < cost[i + 1][j]:
                    cost[i + 1][j], back[i + 1][j] = c + d, (i, j, "del")
            if j < m:  # insert hyp token
                if c + len(h[j]) < cost[i][j + 1]:
                    cost[i][j + 1], back[i][j + 1] = c + len(h[j]), (i, j, "ins")
            if i < n and j < m:  # match / substitute
                s = 0 if r[i] is None else _lev(r[i], h[j])
                if c + s < cost[i + 1][j + 1]:
                    cost[i + 1][j + 1], back[i + 1][j + 1] = c + s, (i, j, "sub")

    sub = ins = dele = 0
    i, j = n, m
    while (i, j) != (0, 0):
        pi, pj, op = back[i][j]
        if op == "sub" and r[pi] is not None:
            sub += _lev(r[pi], h[pj])
        elif op == "ins":
            ins += len(h[pj])
        elif op == "del" and r[pi] is not None:
            dele += len(r[pi])
        i, j = pi, pj
    denom = max(ref_chars, 1)
    return {"cer": round((sub + ins + dele) / denom, 3), "sub": sub, "ins": ins, "dele": dele,
            "ref_chars": ref_chars, "ins_ratio": round(ins / denom, 3), "del_ratio": round(dele / denom, 3)}


def judge(ref: str, hyp: str) -> dict:
    """Error numbers plus flags: high_cer, repeat, omission. `ok` is True when no flag is raised."""
    e = masked_error(ref, hyp)
    flags = []
    if e["cer"] > CER_MAX:
        flags.append("high_cer")
    if e["ins_ratio"] > INS_MAX or (has_repetition(hyp) and not has_repetition(ref)):
        flags.append("repeat")
    if e["del_ratio"] > DEL_MAX:
        flags.append("omission")
    return {**e, "flags": flags, "ok": not flags, "hyp": hyp}


def qa_clip(wav_path: str | Path, reference: str, lang: str, key: str, cache_dir: Path | None = None) -> dict | None:
    """Transcribe the clip back and judge it. Returns None if QA is unknown: the ASR call failed, or every
    reply was junk (a run of one repeated character, see transcribe.is_junk). Junk says nothing about the
    clip, so it must not count as a failed clip and trigger a re-synthesis."""
    r = transcribe_file(Path(wav_path), lang, key, cache_dir)
    if not r["ok"] or r.get("junk"):
        return None
    return {**judge(reference, r["text"]), "cached": r["cached"]}
