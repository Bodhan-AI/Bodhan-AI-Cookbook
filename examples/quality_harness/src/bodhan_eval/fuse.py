"""Fusion (plan §13): turn an item's candidates into flags, each with one confidence, and an item verdict.

Candidates that point at the same place merge into one flag, whatever their categories: agreement is
evidence, and a clause omission one check calls "mistranslation" is still one error. A number missing
inside an omitted sentence lands in the omission's flag the same way (containment).

A flag's confidence is a noisy-OR over check families: each family adds its most trusted candidate's
weight, `weights[signal][category]` = how often that signal pointed at a real error in the labels
(defaults in thresholds.default.json, replaced by calibration, §14). The back-translation checks
are one family: they read the same back-translation, so they don't count twice. The flag's
category is the one with the most weight; the others go to `alt_categories`.

decision = "flag" at confidence >= `flag`, "uncertain" at >= `uncertain`, else "below" (kept in
items.jsonl so every non-flag has its reason). With `adjudicate` on, a flag the adjudicator answered
about (adjudicate.py) is flagged on "yes" (in its category, if it gave one) and "below" on "no". Item verdict: flagged / uncertain / clean / skipped.
MQM-style score = sum of severity points over the flagged flags.
"""

from __future__ import annotations

import json
from pathlib import Path

from .categories import describe

DEFAULT_PATH = Path(__file__).with_name("thresholds.default.json")
SEVERITY = ("minor", "major", "critical")
ITEM_LEVEL = {"length", "speech_rate"}  # about the whole item: never merges with candidates elsewhere
PAD = 0.05  # cross-side matching: slack around a relative position


def load_thresholds(path: Path | None = None) -> dict:
    return json.loads((path or DEFAULT_PATH).read_text(encoding="utf-8"))


def signal(c: dict) -> str:
    """The name a candidate's weight is kept under: its check, the judge's confidence, standalone or not."""
    s = c["check"]
    if s == "judge":
        s += ":" + ("high" if c["score"] >= 0.8 else "medium" if c["score"] >= 0.6 else "low")
    return s + (":strong" if c["standalone"] else "")


def family(c: dict) -> str:
    return "back_translation" if c["check"].startswith("back_translation") else c["check"]


def weight(c: dict, th: dict) -> float:
    w = th["weights"].get(signal(c), {})
    return w.get(c["category"], w.get("*", th["default_weight"]))


def location(c: dict) -> tuple[str, int, int]:
    """Where a candidate points, in the translation when it knows where."""
    if c["side"] == "source" and "translation_span" in c["evidence"]:
        return ("translation", *c["evidence"]["translation_span"])
    return (c["side"], *c["span"])


def places(c: dict) -> list[tuple[str, int, int]]:
    """Every place a candidate is known to point at: its span, and its span on the other side if known."""
    out = [(c["side"], *c["span"])]
    for side in ("translation", "source"):
        if side != c["side"] and f"{side}_span" in c["evidence"]:
            out.append((side, *c["evidence"][f"{side}_span"]))
    return out


def overlaps(a: list[tuple[str, int, int]], b: list[tuple[str, int, int]], lengths: dict[str, int]) -> bool:
    """Compared on a side both know: the character spans overlap (a point, e.g. where text was deleted,
    may touch). No common side: their relative positions in the two texts overlap."""
    same = [(x, y) for x in a for y in b if x[0] == y[0]]
    if same:
        return any(_touch(x, y) for x, y in same)
    # ponytail: relative position stands in for the sentence alignment (§8); store it to match exactly.
    ra, rb = _relative(a[0], lengths), _relative(b[0], lengths)
    return ra[0] <= rb[1] and rb[0] <= ra[1]


def _contained(c: dict, omissions: list[dict]) -> bool:
    """c is part of an omission: inside its span, and not a number that is there but changed."""
    if c["category"] == "omission":
        return True
    return c["evidence"].get("kind") != "changed" and any(
        x[0] == y[0] and y[1] <= x[1] and x[2] <= y[2] for o in omissions for x in places(c) for y in places(o))


def _touch(a, b):
    if a[1] < a[2] and b[1] < b[2]:
        return a[1] < b[2] and b[1] < a[2]
    return a[1] <= b[2] and b[1] <= a[2]


def _relative(loc, lengths):
    n = max(lengths[loc[0]], 1)
    return loc[1] / n - PAD, loc[2] / n + PAD


def noisy_or(weights) -> float:
    p = 1.0
    for w in weights:
        p *= 1 - w
    return 1 - p


def confidence(cands: list[dict], th: dict) -> float:
    best: dict[str, float] = {}
    for c in cands:
        best[family(c)] = max(best.get(family(c), 0.0), weight(c, th))
    return noisy_or(best.values())


def _speech(c: dict) -> bool:
    """Speech and text findings never merge: words missing from the audio are not a translation error."""
    return c["category"].startswith("speech_")


def groups(record: dict, cands: list[dict]) -> list[list[int]]:
    """Indices of `cands` that point at the same place (connected by overlap)."""
    lengths = {"source": len(record["source"]), "translation": len(record["translation"] or "")}
    locs = [places(c) for c in cands]
    parent = list(range(len(cands)))

    def root(i):
        while parent[i] != i:
            i = parent[i]
        return i

    for i in range(len(cands)):
        for j in range(i):
            if cands[i]["check"] not in ITEM_LEVEL and cands[j]["check"] not in ITEM_LEVEL \
                    and _speech(cands[i]) == _speech(cands[j]) and overlaps(locs[i], locs[j], lengths):
                parent[root(i)] = root(j)
    out: dict[int, list[int]] = {}
    for i in range(len(cands)):
        out.setdefault(root(i), []).append(i)
    return list(out.values())


def _ident(c: dict) -> str:
    """Same as adjudicate.ident, as the JSON string the stored answers are keyed by."""
    return json.dumps([c["check"], c["category"], c["side"], list(c["span"])])


def fuse(record: dict, th: dict, candidates: list[dict] | None = None) -> tuple[list[dict], str, int]:
    """(flags, verdict, mqm). `candidates` defaults to the record's; flags index into that list."""
    if "translation" in record["checks"]:
        return [], "skipped", 0
    cands = record["candidates"] if candidates is None else candidates
    answers = {json.dumps(i): a for a in record.get("adjudication", []) if th.get("adjudicate") for i in a["candidates"]}
    flags = []
    for idx in groups(record, cands):
        members = [cands[i] for i in idx]
        by_cat: dict[str, list[dict]] = {}
        for c in members:
            by_cat.setdefault(c["category"], []).append(c)
        ranked = sorted(by_cat, key=lambda k: (-confidence(by_cat[k], th), k))
        if "omission" in by_cat and all(_contained(c, by_cat["omission"]) for c in members):
            ranked.remove("omission")
            ranked.insert(0, "omission")  # containment: what's missing inside a missing sentence is that omission
        conf = round(confidence(members, th), 3)  # rounded first: 1 - (1 - 0.45) is 0.4499..., under a 0.45 threshold
        locs = [location(c) for c in members]
        side = "translation" if any(loc[0] == "translation" for loc in locs) else "source"
        spans = [loc[1:] for loc in locs if loc[0] == side]
        span = [min(s for s, _ in spans), max(e for _, e in spans)]
        text = record["translation"] if side == "translation" else record["source"]
        # The back-translation and length checks don't rate severity: major (MQM's middle) unless another does.
        severity = max((c["severity"] for c in members if c["severity"]), key=SEVERITY.index, default="major")
        decision = "flag" if conf >= th["flag"] else "uncertain" if conf >= th["uncertain"] else "below"
        answer = next((answers[_ident(c)] for c in members if _ident(c) in answers), None)
        if answer is not None:
            decision = "flag" if answer["is_error"] else "below"
            if answer["category"]:
                ranked = [answer["category"], *(k for k in ranked if k != answer["category"])]
            severity = answer["severity"] or severity
        flags.append({
            "category": ranked[0], **describe(ranked[0]), "alt_categories": ranked[1:], "severity": severity,
            "side": side, "span": span, "text": text[span[0]:span[1]],
            "confidence": round(conf, 3), "decision": decision,
            "sources": sorted({c["check"] for c in members}), "candidates": idx,
            **({"adjudicator": {"is_error": answer["is_error"], "reason": answer["reason"]}} if answer else {}),
        })
    flags.sort(key=lambda f: (-f["confidence"], f["side"], f["span"]))
    decisions = {f["decision"] for f in flags}
    verdict = "flagged" if "flag" in decisions else "uncertain" if "uncertain" in decisions else "clean"
    mqm = sum(th["severity_points"][f["severity"]] for f in flags if f["decision"] == "flag")
    return flags, verdict, mqm
