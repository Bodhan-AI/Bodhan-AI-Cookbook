"""Duration-fit math — the core 'hard part'.

Translated speech usually runs longer than the English. We bring it back in
budget with a strict ladder, never an unnatural speed-up:

    1. shorter than slot      -> place and pad with trailing silence
    2. fits within max speed  -> time-stretch (clamped to [0.85, 1.2])
    3. still too long         -> ask the LLM to condense the ENGLISH, re-translate,
                                 re-synth, then re-plan (bounded rounds)
    4. rounds exhausted        -> apply max stretch, accept measured overflow,
                                 borrow adjacent silence (logged, reported)

This module is pure arithmetic over durations, so it is unit-tested directly.
"""

from __future__ import annotations

from dataclasses import dataclass

EPS = 1e-3


@dataclass
class FitPlan:
    action: str          # "pad" | "stretch" | "condense" | "overflow"
    speed: float         # atempo factor to apply (>1 = faster/shorter)
    pad_s: float         # trailing silence to add after speed change
    condense_ratio: float | None  # target English length ratio when action == "condense"
    predicted_dur: float          # resulting audio duration after speed
    fits: bool                    # predicted_dur <= slot (within EPS)


def clamp_speed(speed: float, min_s: float, max_s: float) -> float:
    return max(min_s, min(max_s, speed))


def plan_fit(
    slot_s: float,
    audio_s: float,
    *,
    min_stretch: float = 0.85,
    max_stretch: float = 1.2,
    can_condense: bool = True,
) -> FitPlan:
    """Decide how to fit `audio_s` of speech into a `slot_s` window."""
    slot_s = max(EPS, slot_s)

    # Case 1: already fits -> keep natural speed, pad the remainder.
    if audio_s <= slot_s + EPS:
        return FitPlan("pad", 1.0, max(0.0, slot_s - audio_s), None, audio_s, True)

    needed = audio_s / slot_s  # > 1

    # Case 2: a mild stretch within the speed cap closes the gap.
    if needed <= max_stretch + EPS:
        speed = clamp_speed(needed, min_stretch, max_stretch)
        return FitPlan("stretch", speed, 0.0, None, audio_s / speed, True)

    # Case 3: too long even at max speed -> condense English to a target ratio
    # such that the re-synthesized, max-stretched audio will fit.
    if can_condense:
        target_ratio = max(0.5, (max_stretch * slot_s) / audio_s)
        return FitPlan("condense", max_stretch, 0.0, target_ratio, slot_s, False)

    # Case 4: no condensing left -> max stretch and accept overflow.
    speed = max_stretch
    return FitPlan("overflow", speed, 0.0, None, audio_s / speed, False)


def sync_offset_ms(source_start: float, placed_start: float) -> float:
    return round((placed_start - source_start) * 1000.0, 1)
