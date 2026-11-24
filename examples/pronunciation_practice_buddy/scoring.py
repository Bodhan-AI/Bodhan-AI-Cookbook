"""Word-level pronunciation scoring.

The learner's recording is transcribed by `indic-transcribe`; this module compares
that transcript with the target phrase. It is pure Python (no network), so it is
easy to test.

Honest limit: this scores what the speech recogniser *heard*, not the raw sound.
A word the recogniser could not recognise is treated as "not said clearly".
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

# Sentence punctuation, including the Devanagari/Bengali danda and quotes.
_PUNCT = re.compile(r"[\s.,!?;:\"'()\[\]{}\-।॥‘’“”]+")

# A heard word this similar to the target counts as "close" (partial credit).
CLOSE_THRESHOLD = 0.6
CLOSE_CREDIT = 0.5


def normalize(text: str) -> list[str]:
    """Lower-case, NFC-normalise and split into words, dropping punctuation."""
    text = unicodedata.normalize("NFC", text).lower()
    return [w for w in _PUNCT.split(text) if w]


@dataclass
class WordResult:
    target: str
    heard: str | None
    status: str  # "correct" | "close" | "missed"
    similarity: float


@dataclass
class ScoreResult:
    score: int  # 0-100
    words: list[WordResult]
    extra_words: list[str]  # words heard that are not in the target

    @property
    def perfect(self) -> bool:
        return self.score == 100 and not self.extra_words


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def score_attempt(target: str, heard: str) -> ScoreResult:
    """Compare the target phrase with what was heard, word by word."""
    t_words = normalize(target)
    h_words = normalize(heard)
    results: list[WordResult] = []
    extra: list[str] = []

    matcher = SequenceMatcher(None, t_words, h_words, autojunk=False)
    for op, t1, t2, h1, h2 in matcher.get_opcodes():
        if op == "equal":
            for w in t_words[t1:t2]:
                results.append(WordResult(w, w, "correct", 1.0))
        elif op == "delete":
            for w in t_words[t1:t2]:
                results.append(WordResult(w, None, "missed", 0.0))
        elif op == "insert":
            extra.extend(h_words[h1:h2])
        else:  # replace: pair words positionally, leftovers are missed/extra
            t_chunk, h_chunk = t_words[t1:t2], h_words[h1:h2]
            for i, w in enumerate(t_chunk):
                if i < len(h_chunk):
                    sim = _similarity(w, h_chunk[i])
                    status = "close" if sim >= CLOSE_THRESHOLD else "missed"
                    results.append(WordResult(w, h_chunk[i], status, sim))
                else:
                    results.append(WordResult(w, None, "missed", 0.0))
            extra.extend(h_chunk[len(t_chunk):])

    if not results:
        return ScoreResult(0, [], extra)

    credit = sum(
        1.0 if r.status == "correct" else CLOSE_CREDIT if r.status == "close" else 0.0
        for r in results
    )
    score = round(100 * credit / len(results))
    return ScoreResult(score, results, extra)


def feedback(result: ScoreResult) -> str:
    """One-line encouragement based on the score."""
    if result.perfect:
        return "Perfect! Every word was recognised."
    if result.score >= 80:
        return "Very good. Just polish the highlighted words."
    if result.score >= 50:
        return "Good try. Listen again and repeat the words marked below."
    return "Keep going. Listen slowly, then say it one word at a time."
