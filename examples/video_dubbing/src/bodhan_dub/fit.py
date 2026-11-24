"""Duration-fit logic (pure functions, no I/O).

Every clip is fitted to the REAL speech window of its source unit:
  target  = source speech duration (start -> end)          the ideal voiced length
  budget  = target + up to END_SLACK, only into real silence  the longest acceptable voiced length
Order of tools: shrink/widen the clip's own pauses first, then time-stretch (0.85x - 1.2x),
and only if that is not enough, shorten the English (condense) and re-translate.
"""

from __future__ import annotations

MAX_SPEED = 1.2     # fastest allowed time-stretch
MIN_SPEED = 0.85    # slowest allowed time-stretch
MIN_GAP = 0.2       # silence kept between two dubbed clips (or less, if the source itself has less)
END_SLACK = 0.25    # a dub may end this much after the speaker (only into real silence)
SHORT_TOL = 0.2     # a dub shorter than the speaker by less than this is left alone
SHORT_UNIT = 1.0    # very short units may borrow following silence up to this voiced length


def budget_for(i: int, items: list[dict], video_dur: float) -> dict:
    """items: units sorted by start, with start/end. Returns {target, budget, end_limit}."""
    s, e = items[i]["start"], items[i]["end"]
    nxt = items[i + 1]["start"] if i + 1 < len(items) else video_dur
    gap = max(0.0, nxt - e)
    keep_gap = min(MIN_GAP, gap)
    end_limit = min(e + END_SLACK, nxt - keep_gap)
    budget = end_limit - s
    if budget < SHORT_UNIT:  # a one-word unit cannot be condensed; let it use the following silence
        budget = max(budget, min(SHORT_UNIT, nxt - keep_gap - s))
    ideal = min(budget, (e - s) + END_SLACK)
    return {"target": round(max(e - s, 0.05), 3), "budget": round(max(budget, 0.05), 3),
            "ideal": round(max(ideal, 0.05), 3), "end_limit": round(s + budget, 3)}


def plan_fit(voiced: float, budget: float, target: float, max_shrink: float = 0.0, max_extend: float = 0.0,
             max_speed: float = MAX_SPEED, min_speed: float = MIN_SPEED, ideal: float | None = None) -> dict:
    """Decide how to fit a clip whose sounding part lasts `voiced` seconds.

    Returns {action, pause_delta, speed, final}:
      keep     - fits as is
      fit      - adjust pauses by pause_delta seconds and/or stretch by speed
      condense - still too long at max speed with all pauses shrunk; pause_delta/speed hold the best effort
    `final` is the expected voiced length after fitting.
    `ideal` (default = budget) is where we aim; `budget` is the hard limit (short units may borrow silence).
    """
    aim = budget if ideal is None else min(ideal, budget)
    if voiced > aim:
        shrink = min(max_shrink, voiced - aim)
        rest = voiced - shrink
        if rest <= aim + 1e-9:
            return {"action": "fit", "pause_delta": round(-shrink, 3), "speed": 1.0, "final": round(rest, 3)}
        shrink = max_shrink                       # speech must be sped up: first remove all spare pause time
        rest = voiced - shrink
        speed = rest / aim
        if speed <= max_speed:
            return {"action": "fit", "pause_delta": round(-shrink, 3), "speed": round(speed, 4), "final": round(aim, 3)}
        if rest / max_speed <= budget + 1e-9:     # cannot reach the aim, but fits the hard limit
            return {"action": "fit", "pause_delta": round(-shrink, 3), "speed": max_speed,
                    "final": round(rest / max_speed, 3)}
        return {"action": "condense", "pause_delta": round(-shrink, 3), "speed": max_speed,
                "final": round(rest / max_speed, 3)}
    if voiced < target - SHORT_TOL:
        ext = min(max_extend, target - voiced)
        rest = voiced + ext
        speed = 1.0
        if rest < target - SHORT_TOL:
            speed = max(min_speed, rest / target)
        return {"action": "fit", "pause_delta": round(ext, 3), "speed": round(speed, 4), "final": round(rest / speed, 3)}
    return {"action": "keep", "pause_delta": 0.0, "speed": 1.0, "final": round(voiced, 3)}


def condense_target_chars(en_len: int, voiced: float, budget: float, max_shrink: float = 0.0,
                          aim_speed: float = 1.1) -> int:
    """English length to aim for so the re-synthesized clip needs only ~aim_speed (scaled by measured pace)."""
    factor = (budget * aim_speed) / max(voiced - max_shrink, 1e-6)
    return max(10, int(en_len * min(factor, 1.0)))
