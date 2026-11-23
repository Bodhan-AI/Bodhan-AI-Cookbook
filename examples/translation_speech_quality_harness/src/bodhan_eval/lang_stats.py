"""Per-language robust length statistics derived from the run's own items."""

from collections import defaultdict
from statistics import median
from typing import Iterable

from .checks.registry import LangStats
from .config import CHECK_THRESHOLDS
from .models import Item

_MIN_MAD = 0.01


def compute_lang_stats(items: Iterable[Item]) -> dict[str, LangStats]:
    """Median and MAD of translation/source length ratio, computed per language.

    Languages with too few items keep ``length_stats_reliable=False`` so the length
    check abstains instead of guessing from a pooled or demo-tuned distribution.
    """
    ratios: dict[str, list[float]] = defaultdict(list)
    for item in items:
        if item.translation and item.translation.strip():
            ratios[item.lang].append(len(item.translation) / max(1, len(item.source)))
    min_items = int(CHECK_THRESHOLDS["text_length_min_items"]["value"])
    stats: dict[str, LangStats] = {}
    for lang in sorted(ratios):
        values = ratios[lang]
        center = median(values)
        spread = median(abs(value - center) for value in values)
        stats[lang] = LangStats(
            median_length_ratio=round(center, 6),
            mad_length_ratio=round(max(spread, _MIN_MAD), 6),
            sample_size=len(values),
            length_stats_reliable=len(values) >= min_items,
        )
    return stats
