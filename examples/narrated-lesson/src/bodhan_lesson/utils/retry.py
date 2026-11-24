"""Exponential backoff with jitter."""

from __future__ import annotations

import random


def backoff_delay(attempt: int, base_s: float, max_s: float) -> float:
    """Delay before retry number `attempt` (0-based): exponential, capped, jittered."""
    delay = min(max_s, base_s * (2**attempt))
    return delay * random.uniform(0.5, 1.0)
