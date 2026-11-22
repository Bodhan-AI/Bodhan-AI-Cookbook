"""Deterministic output checks. No LLM, no guessing."""
import unicodedata

from bodhan_mcp.languages import SCRIPT_LANG, detect_language, script_counts

DEVA = {"hi", "mr", "ne", "sa", "mai", "kok", "doi", "brx", "sd", "ks-Deva"}  # sd is Devanagari and sd-Arab Perso-Arabic, as the Bodhan docs name them
ARAB = {"ur", "ks", "sd-Arab"}


def expected_script(lang: str) -> str | None:
    if lang in DEVA:
        return "Devanagari"
    if lang in ARAB:
        return "Arabic"
    if lang in ("bn", "as", "mni-Beng"):
        return "Bengali"
    if lang == "mni":  # Manipuri defaults to Meitei Mayek; check_translation also accepts Bengali script for it
        return "MeeteiMayek"
    return {v: k for k, v in SCRIPT_LANG.items()}.get(lang)


def _norm(s: str) -> str:
    """Letters, vowel signs (marks) and digits only. Python's \\w drops Indic vowel signs, so filter by category."""
    s = unicodedata.normalize("NFC", s).lower()
    return "".join(ch for ch in s if unicodedata.category(ch)[0] in "LMN")


def tokens(s: str) -> list[str]:
    """Words made of letters, vowel signs and digits. Invisible joiners (ZWJ/ZWNJ) are dropped, not treated as spaces:
    Telugu, Kannada and Malayalam text uses them inside words and engines disagree on where."""
    s = unicodedata.normalize("NFC", s).lower()
    return "".join("" if unicodedata.category(ch) == "Cf" else ch if unicodedata.category(ch)[0] in "LMN" else " " for ch in s).split()


def omitted(ref: str, hyp: str, min_run: int = 3) -> tuple[list[str], float]:
    """Reference words the hypothesis lost: pure deletions of >= min_run words, and replaced blocks where the hypothesis
    is at least min_run words shorter (a dropped sentence next to a misheard word). Digits ignored: engines spell them out."""
    import difflib
    r, h = tokens(ref), tokens(hyp)
    sm = difflib.SequenceMatcher(None, r, h, autojunk=False)
    runs, lost = [], 0
    for tag, a, b, c, d in sm.get_opcodes():
        gone = (b - a) if tag == "delete" else (b - a) - (d - c) if tag == "replace" else 0
        if gone >= min_run and not all(w.isdigit() for w in r[a:b]):
            runs.append(" ".join(r[a:b]))
            lost += gone
    return runs, lost / max(1, len(r))


def chunk_recall(chunk: str, hyp: str, n: int = 3) -> float:
    """Share of one chunk's character n-grams found in the back-transcript. A chunk the speech engine dropped scores near 0
    even when the whole text still looks 95 percent right. Very short chunks are not judged (returns 1.0)."""
    from collections import Counter
    a, b = _norm(chunk), _norm(hyp)
    if len(a) < n * 3:
        return 1.0
    ca = Counter(a[i:i + n] for i in range(len(a) - n + 1))
    cb = Counter(b[i:i + n] for i in range(max(1, len(b) - n + 1)))
    return sum((ca & cb).values()) / sum(ca.values())


def similarity(a: str, b: str, n: int = 3) -> float:
    """Character n-gram F1 on normalised text (robust to punctuation/spacing)."""
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    ga = [a[i:i + n] for i in range(max(1, len(a) - n + 1))]
    gb = [b[i:i + n] for i in range(max(1, len(b) - n + 1))]
    from collections import Counter
    ca, cb = Counter(ga), Counter(gb)
    ov = sum((ca & cb).values())
    p, r = ov / len(gb), ov / len(ga)
    return 0.0 if ov == 0 else 2 * p * r / (p + r)


def check_translation(src: str, out: str, target: str, mode: str, warnings: list[str], script: str = "native") -> dict:
    problems = []
    if any("truncated" in w for w in warnings):
        problems.append("output truncated")
    if any("structure changed" in w for w in warnings):
        problems.append("markdown structure changed")
    ratio = len(out) / max(1, len(src))
    if not (0.4 if len(src) >= 300 else 0.3) <= ratio <= 4.0:
        problems.append(f"length ratio {ratio:.2f} out of range")
    s_lines = [x for x in src.splitlines() if x.strip()]
    o_lines = [x for x in out.splitlines() if x.strip()]
    if mode == "plain" and len(s_lines) > 1 and len(o_lines) < 0.8 * len(s_lines):
        problems.append(f"lost lines: {len(s_lines)} source lines became {len(o_lines)}")
    unchanged = any("returned unchanged" in w for w in warnings)
    if unchanged:
        det = detect_language(src)
        if det["language"] != target and det["confidence"] >= 0.6:
            problems.append(f"returned unchanged although the source looks like '{det['language']}', not '{target}'")
    sc = script_counts(out)
    exp = expected_script(target)
    if script == "native" and exp and sc and max(sc, key=sc.get) not in {exp, *(["Bengali"] if target == "mni" else [])} and not unchanged:
        problems.append(f"output script {max(sc, key=sc.get)} != expected {exp}")
    if not out.strip():
        problems.append("empty output")
    return {"ok": not problems, "problems": problems, "length_ratio": round(ratio, 2)}


def check_cues(src: list[dict], out: list[dict], target: str) -> dict:
    """Translated subtitle cues must match the source cue-for-cue (count, timing) and be in the target script."""
    problems = []
    if len(src) != len(out):
        problems.append(f"cue count {len(src)} -> {len(out)}")
    else:
        if any((a["start"], a["end"]) != (b["start"], b["end"]) for a, b in zip(src, out)):
            problems.append("cue timings changed")
        empty = sum(1 for a, b in zip(src, out) if a["text"].strip() and not b["text"].strip())
        if empty:
            problems.append(f"{empty} cue(s) became empty")
    joined = " ".join(c["text"] for c in out)
    sc, exp = script_counts(joined), expected_script(target)
    if exp and sc and max(sc, key=sc.get) not in {exp, *(["Bengali"] if target == "mni" else [])}:
        problems.append(f"output script {max(sc, key=sc.get)} != expected {exp}")
    return {"ok": not problems, "problems": problems, "cues": len(out)}


def coverage(src_head: str, back: str) -> float:
    """Share of the source's content words (4+ letters) that reappear after translating there and back."""
    words = {w for w in tokens(src_head) if len(w) >= 4 and not w.isdigit()}
    got = set(tokens(back))
    return len(words & got) / max(1, len(words))
