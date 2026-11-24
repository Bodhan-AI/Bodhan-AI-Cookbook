"""Timeline placement: put each fitted clip so its FIRST SOUND lands on the source speech start.

No planned lateness: a clip is delayed only when the previous clip is still sounding.
Clips are already fitted (pauses + speed) before placement, so this step never changes speed.
"""

from __future__ import annotations

from .fit import MIN_GAP

SYNC_TOL = 0.3    # target: a dubbed segment starts within +-300 ms of its source segment
DELAY_FLAG = 0.15  # report any forced delay above this
END_FLAG = 0.5     # report dubs ending this much before/after the speaker


def schedule(items: list[dict], video_dur: float, min_gap: float = MIN_GAP) -> list[dict]:
    """items sorted by start: [{id, start, end, dur, lead, voiced_end}]
      start/end      source speech window
      dur            clip file length;  lead / voiced_end: first / last sound inside the clip (s)
    Returns [{id, placed_start, placed_end, onset, voiced_end_at, delay, start_err, end_err, flags}].
    """
    out: list[dict] = []
    prev_sound_end, prev_src_end = float("-inf"), None
    for it in items:
        flags: list[str] = []
        gap_needed = min(min_gap, max(0.0, it["start"] - prev_src_end)) if prev_src_end is not None else 0.0
        onset = max(it["start"], prev_sound_end + gap_needed)  # silent lead may fall before t=0; it is trimmed
        delay = onset - it["start"]
        if delay > DELAY_FLAG:
            flags.append("delayed")
        if delay > SYNC_TOL:
            flags.append("sync_miss")
        placed = onset - it["lead"]
        sound_end = placed + it["voiced_end"]
        end_err = sound_end - it["end"]
        if end_err > END_FLAG:
            flags.append("late_end")
        elif end_err < -END_FLAG:
            flags.append("early_end")
        if placed + it["dur"] > video_dur + 1e-6:
            flags.append("past_end")
        out.append({"id": it["id"], "placed_start": round(placed, 3), "placed_end": round(placed + it["dur"], 3),
                    "onset": round(onset, 3), "voiced_end_at": round(sound_end, 3), "delay": round(delay, 3),
                    "start_err": round(delay, 3), "end_err": round(end_err, 3), "flags": flags})
        prev_sound_end, prev_src_end = sound_end, it["end"]
    return out
