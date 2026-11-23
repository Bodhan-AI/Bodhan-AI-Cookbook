"""Triage for the costly speech (ASR) check.

Deterministic policy (an LLM advisor is optional and only reorders within budget):
- Every item already looking suspicious from cheaper stages is transcribed.
- Of the rest, a stratified deterministic sample (hash of id) per language and length bucket
  is transcribed, sized by --speech-sample-rate.
Determinism: selection depends only on ids + rate + seed, never on wall-clock or ordering.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from .models import Item


def _bucket(item: Item) -> str:
    n = len(item.source or "")
    if n < 80:
        return "short"
    if n < 300:
        return "medium"
    return "long"


def _hash_unit(item_id: str, seed: int) -> float:
    h = hashlib.sha256(f"{seed}:{item_id}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def select_for_asr(items: Iterable[Item], suspicious_ids: set[str], sample_rate: float,
                   seed: int = 0, max_items: int | None = None) -> set[str]:
    items = list(items)
    selected: set[str] = set()
    for it in items:
        if it.id in suspicious_ids:
            selected.add(it.id)

    # stratified sample of the remainder
    if sample_rate > 0:
        strata: dict[tuple[str, str], list[Item]] = {}
        for it in items:
            if it.id in selected:
                continue
            strata.setdefault((it.lang, _bucket(it)), []).append(it)
        for _, group in sorted(strata.items()):
            group_sorted = sorted(group, key=lambda it: _hash_unit(it.id, seed))
            k = len(group) if sample_rate >= 1 else max(0, round(len(group) * sample_rate))
            k = int(k)
            for it in group_sorted[:k]:
                selected.add(it.id)

    if max_items is not None and len(selected) > max_items:
        # keep suspicious first, then lowest-hash sampled, deterministically
        ordered = sorted(selected, key=lambda i: (i not in suspicious_ids, _hash_unit(i, seed)))
        selected = set(ordered[:max_items])
    return selected
