"""Quality checks on dubbed audio: back-transcription error rate, repeats, omissions, numbers."""
import re
import unicodedata


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFC", s).lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def edit_distance(a: str, b: str) -> int:
    if not a:
        return len(b)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str) -> float:
    r, h = normalize(ref), normalize(hyp)
    if not r:
        return 0.0 if not h else 1.0
    return edit_distance(r, h) / len(r)


def has_repeat(hyp: str, ref: str, min_words: int = 3) -> bool:
    """True if the back-transcript contains a phrase repeated back to back that the reference does not."""
    h = normalize(hyp).split()
    for n in range(min_words, max(min_words, len(h) // 2) + 1):
        for i in range(len(h) - 2 * n + 1):
            if h[i:i + n] == h[i + n:i + 2 * n]:
                phrase = " ".join(h[i:i + n])
                if normalize(ref).count(phrase) < 2:
                    return True
    return False


def to_ascii_digits(s: str) -> str:
    return "".join(str(unicodedata.digit(c)) if c.isdigit() else c for c in s)


def numbers(s: str) -> list[str]:
    return re.findall(r"\d+(?:[.,]\d+)*", to_ascii_digits(s))


def numbers_preserved(en: str, tr: str) -> list[str]:
    """English numerals that do not appear in the translation (as digits in any script)."""
    have = {n.replace(",", "") for n in numbers(tr)}
    return [n for n in numbers(en) if n.replace(",", "") not in have]


def assess(translation: str, back: str, cer_fail: float | None = None, ratio_hi: float = 1.45, ratio_lo: float = 0.6) -> dict:
    """Judge one dubbed segment from its back-transcript. Returns metrics and flags."""
    from . import config
    cer_fail = config.QA_CER_FAIL if cer_fail is None else cer_fail
    c = cer(translation, back)
    nr, nb = len(normalize(translation)), len(normalize(back))
    ratio = nb / nr if nr else 1.0
    flags = []
    if has_repeat(back, translation) or ratio > ratio_hi:
        flags.append("repeat")
    if ratio < ratio_lo:
        flags.append("omission")
    if c > cer_fail and not flags:
        flags.append("mismatch")
    return {"cer": round(c, 4), "length_ratio": round(ratio, 3), "flags": flags, "ok": not flags}
