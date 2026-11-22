"""Parallelize per-language API work (I/O-bound).

Bodhan/Gemini calls spend most time waiting on the network, so a small
ThreadPoolExecutor across languages cuts wall time. Cap workers to stay
under hosted concurrency / RPM limits.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Sequence, TypeVar

DEFAULT_MAX_LANG_WORKERS = 3

T = TypeVar("T")
R = TypeVar("R")


def get_max_lang_workers() -> int:
    """Max concurrent languages (default 3). Override with LANG_WORKERS."""
    raw = os.environ.get("LANG_WORKERS") or os.environ.get("BODHAN_LANG_WORKERS")
    if raw is None or not str(raw).strip():
        return DEFAULT_MAX_LANG_WORKERS
    try:
        value = int(str(raw).strip())
    except ValueError:
        return DEFAULT_MAX_LANG_WORKERS
    return max(1, min(value, 8))


def map_parallel(
    items: Sequence[T],
    fn: Callable[[T], R],
    *,
    max_workers: int | None = None,
) -> list[R]:
    """Run ``fn`` on each item; preserve input order.

    Uses one worker when there is a single item (no thread overhead).
    """
    if not items:
        return []
    if len(items) == 1:
        return [fn(items[0])]

    workers = max_workers if max_workers is not None else get_max_lang_workers()
    workers = max(1, min(workers, len(items)))
    if workers == 1:
        return [fn(item) for item in items]

    slots: list[R | None] = [None] * len(items)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_index = {
            pool.submit(fn, item): index for index, item in enumerate(items)
        }
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            slots[index] = future.result()
    results: list[R] = []
    for slot in slots:
        assert slot is not None
        results.append(slot)
    return results
