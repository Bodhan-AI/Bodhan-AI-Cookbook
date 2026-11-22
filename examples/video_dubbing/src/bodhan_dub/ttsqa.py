"""Judge a synthesized clip from its back-transcription: did Indic-Speak say exactly the text?

Plain character error rate (CER) misfires on two things the TTS legitimately says
differently from how the text is written:
  * numbers: "180" is spoken as words (often in English: "one hundred and eighty"),
  * Latin-script words in code-mixed text: "reflex" comes back as Devanagari रिफ्लेक्स.
So digit runs and Latin runs in the reference become bounded wildcards that may match
any spoken form up to a plausible length. Everything else is compared character by
character, so real repetitions and omissions still show.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from .textcheck import ascii_digits

CER_LIMIT = 0.25
_WILD = re.compile(r"[0-9]+(?:[.,][0-9]+)*|[A-Za-z]+(?:['-][A-Za-z]+)*")


def _keep(ch: str) -> bool:
    return unicodedata.category(ch)[0] not in "PSZ" and not ch.isspace()


def normalize(text: str) -> str:
    return "".join(ch for ch in ascii_digits(unicodedata.normalize("NFC", text)).casefold() if _keep(ch))


def reference_tokens(text: str) -> list[tuple[str, int]]:
    """Reference as (char, 0) for exact characters and ("*", max_len) for wildcards."""
    text = ascii_digits(unicodedata.normalize("NFC", text))
    tokens: list[tuple[str, int]] = []
    pos = 0
    for m in _WILD.finditer(text):
        tokens += [(ch, 0) for ch in text[pos:m.start()].casefold() if _keep(ch)]
        run = m.group()
        # A number is spoken as several words; a Latin word comes back about as long, in another script.
        bound = 12 * len(re.sub(r"\D", "", run)) + 6 if run[0].isdigit() else 2 * len(run) + 4
        tokens.append(("*", bound))
        pos = m.end()
    tokens += [(ch, 0) for ch in text[pos:].casefold() if _keep(ch)]
    return tokens


def wildcard_cer(reference: str, hypothesis: str) -> float:
    """Edit distance over exact reference characters; wildcards absorb up to their bound for free."""
    ref, hyp = reference_tokens(reference), normalize(hypothesis)
    exact = sum(1 for _, bound in ref if bound == 0)
    if exact == 0:
        return 0.0
    inf = float("inf")
    prev = [float(j) for j in range(len(hyp) + 1)]  # hypothesis characters before any reference: insertions
    for ch, bound in ref:
        cur = [inf] * (len(hyp) + 1)
        if bound:
            # wildcard: consume 0..bound hypothesis characters at no cost
            for j in range(len(hyp) + 1):
                cur[j] = min(prev[k] for k in range(max(0, j - bound), j + 1))
        else:
            cur[0] = prev[0] + 1
            for j in range(1, len(hyp) + 1):
                cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ch != hyp[j - 1]))
        prev = cur
    return prev[-1] / exact


def words(text: str) -> list[str]:
    # Letters, combining marks (Indic vowel signs) and digits make words; everything else separates.
    cleaned = "".join(ch if unicodedata.category(ch)[0] in "LMN" else " "
                      for ch in ascii_digits(unicodedata.normalize("NFC", text)).casefold())
    return cleaned.split()


def plain_cer(reference: str, hypothesis: str) -> float:
    """Ordinary CER on normalised text (no wildcards), reported next to the number-aware CER.

    It overstates errors wherever numbers or English words are spoken rather than written.
    """
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)


def repeated_phrase(reference: str, hypothesis: str, n: int = 4) -> str | None:
    """A run of the text's own words that the clip says more often than the text does: a TTS loop.

    Only n-grams made entirely of reference words count, so a number spoken as words
    ("hundred and eighty" twice for two different numbers) can't look like a loop.
    """
    vocab = set(words(reference))

    def grams(text: str) -> Counter[tuple[str, ...]]:
        w = words(text)
        return Counter(tuple(w[i:i + n]) for i in range(len(w) - n + 1))

    said, written = grams(hypothesis), grams(reference)
    loops = [g for g, c in said.items() if c >= 2 and c > written[g] and set(g) <= vocab]
    return " ".join(loops[0]) if loops else None


@dataclass(frozen=True)
class Verdict:
    cer: float
    issue: str  # "" when fine, else repetition / omission / mismatch / empty audio
    detail: str = ""


def judge_clip(reference: str, hypothesis: str, duration_s: float) -> Verdict:
    if duration_s < 0.2 or not normalize(hypothesis):
        return Verdict(1.0, "empty audio" if duration_s < 0.2 else "omission", "nothing intelligible was heard")
    error = wildcard_cer(reference, hypothesis)
    loop = repeated_phrase(reference, hypothesis)
    if loop:
        return Verdict(error, "repetition", f"said more than once: '{loop}'")
    if error <= CER_LIMIT:
        return Verdict(error, "")
    tokens = reference_tokens(reference)
    exact = sum(1 for _, b in tokens if b == 0)
    heard = len(normalize(hypothesis))
    if heard < exact:  # shorter than the text even before numbers and English words are spoken
        return Verdict(error, "omission", f"heard {heard} characters for at least {exact} expected")
    if heard > 1.6 * (exact + sum(b // 2 for _, b in tokens)):
        return Verdict(error, "repetition", f"heard {heard} characters, far more than the text")
    return Verdict(error, "mismatch", "the clip does not match the text")
