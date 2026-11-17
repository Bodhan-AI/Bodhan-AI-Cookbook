"""Turn candidates into flags.

1. Group candidates of one item that point at the same place (or, without a position, the same kind of problem).
2. Decide each group from its evidence: exact checks may flag alone; noisy checks need a second, independent signal;
   weak signals only support; the judge confirms or rejects noisy evidence.
3. Merge each flagged group into one flag: one category, one severity, the tightest trustworthy spans, all evidence.
"""

import re

SPAN_SIDE = "source"  # which side `span` reports; both sides are always written

SPEECH = {"speech_omission", "speech_repetition", "garbled"}
TEXT_PRECEDENCE = ["format", "wrong_language", "number", "term", "omission", "addition", "mistranslation"]
SPEECH_PRECEDENCE = ["garbled", "speech_omission", "speech_repetition"]
DEFAULT_SEVERITY = {c: "major" for c in TEXT_PRECEDENCE + SPEECH_PRECEDENCE}
_RANK = {"exact": 0, "judge": 1, "noisy": 2, "weak": 3}
_SEV = {"minor": 0, "major": 1, "critical": 2}


def _overlap(a, b):
    return a is not None and b is not None and a[0] < b[1] and b[0] < a[1]


def _width(span):
    return span[1] - span[0] if span else 10**9


def group(candidates: list[dict]) -> list[list[dict]]:
    """Union candidates that share a position (same side) within the same area (text or speech)."""
    n = len(candidates)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def area(c):
        return "speech" if c["category"] in SPEECH else "text"

    for i in range(n):
        for j in range(i + 1, n):
            a, b = candidates[i], candidates[j]
            if area(a) == area(b) and (_overlap(a["translation_span"], b["translation_span"]) or _overlap(a["source_span"], b["source_span"])):
                parent[find(i)] = find(j)
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = [[candidates[i] for i in g] for g in groups.values()]
    # Candidates with no position join a positioned group of the same category in the same area, else stand alone.
    positioned = [g for g in out if any(c["translation_span"] or c["source_span"] for c in g)]
    loose = [g[0] for g in out if len(g) == 1 and not (g[0]["translation_span"] or g[0]["source_span"])]
    result = positioned + []
    for c in loose:
        home = next((g for g in positioned if any(x["category"] == c["category"] for x in g)), None)
        if home is None and area(c) == "speech":  # unpositioned speech evidence describes the whole recording
            home = next((g for g in result if all(area(x) == "speech" for x in g)), None)
        if home is not None:
            home.append(c)
        else:
            result.append([c])
    return result


def decide(g: list[dict], judge_verdict: str | None) -> str:
    """'flag', 'review' or 'drop' for one group."""
    classes = {c["class"] for c in g}
    noisy_checks = {c["check"] for c in g if c["class"] == "noisy"}
    judge_agreed = [c for c in g if c["class"] == "judge"]
    if "exact" in classes:
        return "flag"
    if judge_agreed:
        return "flag" if any(c["severity_hint"] in ("major", "critical") for c in judge_agreed) else "review"
    if len(noisy_checks) >= 2:
        return "review" if judge_verdict == "clean" else "flag"  # two independent signals against a clean verdict: ask a human
    if noisy_checks:
        if judge_verdict == "clean":
            return "drop"  # one noisy signal, rejected by the judge in every pass: checker noise
        return "flag" if "weak" in classes else "review"
    return "drop"


def merge(g: list[dict]) -> dict:
    g = sorted(g, key=lambda c: (_RANK[c["class"]], -c["strength"], c["check"]))
    deciding = [c for c in g if c["class"] != "weak"] or g
    speech = all(c["category"] in SPEECH for c in g)
    order = SPEECH_PRECEDENCE if speech else TEXT_PRECEDENCE
    exact_cats = [c["category"] for c in deciding if c["class"] == "exact"]
    judge_cats = [c["category"] for c in deciding if c["class"] == "judge"]
    if exact_cats:
        category = min(exact_cats, key=lambda x: order.index(x) if x in order else 99)
    elif judge_cats:  # the judge reads meaning; prefer its category over the diff's guess
        category = judge_cats[0]
    else:
        # Noisy only: the category most independent checks agree on ("number missing" inside a dropped sentence is
        # the omission's side effect); precedence breaks ties.
        votes = {cat: len({c["check"] for c in deciding if c["category"] == cat}) for cat in {c["category"] for c in deciding}}
        category = min(votes, key=lambda x: (-votes[x], order.index(x) if x in order else 99))
    judged = [c["severity_hint"] for c in deciding if c["class"] == "judge" and c["severity_hint"]]
    hints = judged or [c["severity_hint"] for c in deciding if c["severity_hint"]]
    severity = max(hints, key=_SEV.get) if hints else DEFAULT_SEVERITY.get(category, "major")

    def best(field):
        # Spans from the flag's own category first: a number inside an added sentence must not shrink the addition's span.
        own = [c for c in deciding if c[field] and c["category"] == category]
        with_span = own or [c for c in deciding if c[field]]
        if not with_span:
            return None
        top = min(_RANK[c["class"]] for c in with_span)
        return min((c for c in with_span if _RANK[c["class"]] == top), key=lambda c: _width(c[field]))[field]

    source_span, translation_span = best("source_span"), best("translation_span")
    primary = source_span if SPAN_SIDE == "source" else translation_span
    span_in = SPAN_SIDE if primary else ("translation" if SPAN_SIDE == "source" else "source")
    miss = 1.0
    for check in {c["check"] for c in deciding}:
        miss *= 1 - max(c["strength"] for c in deciding if c["check"] == check)
    audio = next((c.get("audio_span_s") for c in g if c.get("audio_span_s")), None)
    return {"category": category, "severity": severity, "confidence": round(min(0.99, 1 - miss), 3),
            "span": primary or translation_span or source_span, "span_in": span_in,
            "source_span": source_span, "translation_span": translation_span, "audio_span_s": audio,
            "checks": sorted({c["check"] for c in g}),
            "evidence": [{"check": c["check"], "class": c["class"], "category": c["category"], **c["evidence"]} for c in g]}


def combine(candidates: list[dict], judge_verdict: str | None = None) -> tuple[str, list[dict], list[dict], int]:
    """Returns (status, flags, review, dropped_as_noise)."""
    flags, review, dropped = [], [], 0
    for g in group(candidates):
        d = decide(g, judge_verdict)
        if d == "flag":
            flags.append(merge(g))
        elif d == "review":
            review.append(merge(g))
        elif any(c["class"] == "noisy" for c in g):
            dropped += 1
    key = lambda f: (f["span"] or [0, 0], f["category"])  # noqa: E731
    flags.sort(key=key)
    review.sort(key=key)
    return ("flagged" if flags else "review" if review else "clean"), flags, review, dropped


_NEGATION = re.compile(r"\b(not|never|no|none|nothing|without|cannot|neither|nor)\b|n't\b", re.I)


def adjust_severity(flag: dict, source: str, translation: str) -> dict:
    """Code severity rules on top of the merged flag: a negation lost or altered is critical; an omission or
    addition of a single word, with no judge severity, is minor."""
    sv, tv = flag.get("source_span"), flag.get("translation_span")
    src_text = source[sv[0]:sv[1]] if sv else ""
    if flag["category"] in ("omission", "addition", "mistranslation") and _NEGATION.search(src_text):
        flag["severity"], flag["severity_reason"] = "critical", "negation involved"
        return flag
    judged = any(e["class"] == "judge" for e in flag["evidence"])
    text = src_text if flag["category"] == "omission" else (translation[tv[0]:tv[1]] if tv else "")
    if flag["category"] in ("omission", "addition") and not judged and text and len(text.split()) == 1:
        flag["severity"], flag["severity_reason"] = "minor", "a single word"
    return flag
