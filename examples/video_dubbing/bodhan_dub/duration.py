"""Duration budgeting for dubbed speech.

The central problem of dubbing: a translated sentence is rarely the same length
as the English it replaces. Hindi is usually longer. If you simply lay it on the
timeline, every following sentence drifts later and the dub desynchronises.

The policy here is a ladder, applied in order, and it never hides a miss:

1. **Fits.** Place it as-is.
2. **Slight overrun.** Speed it up with ``atempo``, but never past 1.20x.
3. **Still over.** Let it spill into the following silence, up to a limit.
4. **Still over.** Accept the overrun and record ``status: "miss"`` with the
   number of milliseconds it will bleed past its slot.

Step 3 exists because speech is not evenly spaced: a short sentence is often
followed by a pause, so a small overrun is invisible to a listener.

Speech rates are measured, never assumed. Each run starts from a per-language
default and then calibrates from the clips it has already synthesised, so an
unusually fast or slow speaker converges within a few segments.
"""

from __future__ import annotations

from dataclasses import dataclass

# Characters of script per second of speech. Starting points only; every run
# re-estimates from real audio.
DEFAULT_CHARS_PER_SECOND: dict[str, float] = {
    "hi": 13.0,
    "bn": 11.5,
    "ta": 9.5,
    "te": 10.0,
    "kn": 10.0,
    "ml": 10.5,
    "mr": 12.0,
    "gu": 11.5,
    "pa": 11.0,
    "or": 11.0,
    "as": 10.5,
    "ur": 11.5,
}
_FALLBACK_RATE = 11.0

MIN_CALIBRATION_CHARS = 60
MIN_CALIBRATION_SAMPLES = 2
OBSERVED_WEIGHT = 0.85


class SpeakingRate:
    """Per-language, self-calibrating characters-per-second estimate."""

    def __init__(self, lang: str, default: float | None = None) -> None:
        self.lang = lang
        self._prior = default if default is not None else DEFAULT_CHARS_PER_SECOND.get(lang, _FALLBACK_RATE)
        self._chars = 0
        self._seconds = 0.0
        self._samples = 0

    @property
    def rate(self) -> float:
        if self._samples < MIN_CALIBRATION_SAMPLES or self._chars < MIN_CALIBRATION_CHARS:
            return self._prior
        observed = self._chars / max(self._seconds, 1e-6)
        return OBSERVED_WEIGHT * observed + (1 - OBSERVED_WEIGHT) * self._prior

    @property
    def samples(self) -> int:
        return self._samples

    def predict_seconds(self, characters: int) -> float:
        return max(0.0, characters) / max(self.rate, 1e-6)

    def observe(self, characters: int, seconds: float) -> None:
        if characters <= 0 or seconds <= 0:
            return
        self._chars += characters
        self._seconds += seconds
        self._samples += 1

    def to_dict(self) -> dict[str, float | int]:
        return {
            "chars_per_second": round(self.rate, 3),
            "samples": self._samples,
            "observed_seconds": round(self._seconds, 3),
        }


@dataclass(frozen=True)
class Fit:
    tempo: float
    spill_s: float
    status: str  # ok | stretched | spilled | miss
    overrun_s: float

    def to_dict(self) -> dict[str, object]:
        return {
            "tempo": round(self.tempo, 3),
            "spill_s": round(self.spill_s, 3),
            "status": self.status,
            "overrun_s": round(self.overrun_s, 3),
        }


def fit(
    actual_s: float,
    budget_s: float,
    *,
    max_tempo: float = 1.20,
    max_spill_s: float = 0.80,
) -> Fit:
    """Decide how to place a clip that takes ``actual_s`` in a ``budget_s`` slot."""
    if budget_s <= 0:
        return Fit(tempo=1.0, spill_s=0.0, status="miss", overrun_s=actual_s)
    if actual_s <= budget_s:
        return Fit(tempo=1.0, spill_s=0.0, status="ok", overrun_s=0.0)

    needed = actual_s / budget_s
    if needed <= max_tempo:
        return Fit(tempo=needed, spill_s=0.0, status="stretched", overrun_s=0.0)

    # At the maximum legal tempo, how far past the slot do we still land?
    remaining = actual_s / max_tempo - budget_s
    if remaining <= max_spill_s:
        return Fit(tempo=max_tempo, spill_s=remaining, status="spilled", overrun_s=remaining)

    overrun = actual_s / max_tempo - budget_s
    return Fit(tempo=max_tempo, spill_s=max_spill_s, status="miss", overrun_s=overrun)
