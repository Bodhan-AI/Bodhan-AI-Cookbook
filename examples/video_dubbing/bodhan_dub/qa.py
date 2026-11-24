"""Checks applied to generated speech before it is accepted.

``indic-speak`` is documented to occasionally repeat or omit content. A pipeline
that ships that silently is worse than one that fails loudly, so every clip is
back-transcribed with ``indic-transcribe`` and compared against the text that
was requested.

Three cheap signals, one extra model call per clip:

* **character error rate** — did it say roughly what we asked?
* **repetition** — did a phrase loop? (detected by the same n-gram collapse
  used on ASR output)
* **truncation** — did it stop early?

The back-transcriber makes its own mistakes, so a failure is a *trigger to
retry*, not a verdict. If a retry produces the same audio and the same
transcript, the likely culprit is the checker, and the caller records that.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import text

DEFAULT_CER_THRESHOLD = 0.25
DEFAULT_MIN_TRUNCATION = 0.60

# Character error rate is not meaningful on a handful of characters: one wrong
# glyph moves it by 20-30%. Below this length only truncation and repetition are
# trusted, so a five-character clip is not retried four times over noise.
SHORT_TEXT_CHARS = 12


@dataclass(frozen=True)
class SpeechQA:
    cer: float
    repetition: bool
    truncation: float
    verdict: str  # pass | fail
    reason: str | None
    heard: str

    def to_dict(self) -> dict[str, object]:
        return {
            "cer": round(self.cer, 4),
            "repetition": self.repetition,
            "truncation": round(self.truncation, 3),
            "verdict": self.verdict,
            "reason": self.reason,
        }


def evaluate(
    expected: str,
    heard: str,
    *,
    cer_threshold: float = DEFAULT_CER_THRESHOLD,
    min_truncation: float = DEFAULT_MIN_TRUNCATION,
) -> SpeechQA:
    heard_clean = text.normalize_ws(heard)
    if text.is_degenerate(heard_clean):
        return SpeechQA(
            cer=1.0,
            repetition=False,
            truncation=0.0,
            verdict="fail",
            reason="back-transcription was empty or punctuation only",
            heard=heard_clean,
        )

    _, looped = text.collapse_repeats(heard_clean, min_words=3, max_repeats=2)
    cer = text.char_error_rate(expected, heard_clean)
    truncation = text.truncation_ratio(expected, heard_clean)
    comparable = len(text.normalize_for_compare(expected))

    reasons: list[str] = []
    if looped:
        reasons.append("repeated phrase")
    if truncation < min_truncation:
        reasons.append(f"truncated ({truncation:.0%} of expected length heard)")
    if cer > cer_threshold and comparable >= SHORT_TEXT_CHARS:
        reasons.append(f"character error rate {cer:.2f}")

    verdict = "fail" if reasons else "pass"
    return SpeechQA(
        cer=cer,
        repetition=looped,
        truncation=truncation,
        verdict=verdict,
        reason="; ".join(reasons) or None,
        heard=heard_clean,
    )
