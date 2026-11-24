from ..config import CHECK_DEFAULTS, CHECK_THRESHOLDS
from ..models import Flag, Item
from .registry import LangStats, register_check


@register_check(
    name="text_length",
    categories=["omission", "addition"],
    modality="text",
    cost_tier=0,
)
def check_length(item: Item, stats: LangStats) -> list[Flag]:
    if not item.translation or not item.source:
        return []
    if not stats.length_stats_reliable:
        return []
    ratio = len(item.translation) / max(1, len(item.source))
    multiplier = CHECK_THRESHOLDS["text_length_mad_multiplier"]["value"]
    cutoff = stats.median_length_ratio + multiplier * stats.mad_length_ratio
    floor = max(
        CHECK_THRESHOLDS["text_length_min_ratio"]["value"],
        stats.median_length_ratio - multiplier * stats.mad_length_ratio,
    )
    if floor <= ratio <= cutoff:
        return []
    category = "addition" if ratio > cutoff else "omission"
    return [Flag(
        category=category,
        severity="minor",
        modality="text",
        span=(0, len(item.translation)) if category == "addition" else None,
        source_span=(0, len(item.source)) if category == "omission" else None,
        evidence={"length_ratio": round(ratio, 6), "median": stats.median_length_ratio, "mad": stats.mad_length_ratio},
        detectors=["text_length"],
        confidence=CHECK_DEFAULTS["confidence"]["text_length"],
        suppressed_reason="single_noisy_signal",
    )]
