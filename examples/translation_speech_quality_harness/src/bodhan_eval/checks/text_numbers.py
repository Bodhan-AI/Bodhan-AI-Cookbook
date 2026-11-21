from collections import Counter

from ..config import CHECK_DEFAULTS
from .registry import LangStats, register_check
from ..models import Flag, Item
from ..normalize import extract_numbers


@register_check(name="text_numbers", categories=["number"], modality="text", cost_tier=0)
def check_numbers(item: Item, stats: LangStats | None = None) -> list[Flag]:
    del stats
    if not item.translation:
        return []
    source_numbers = extract_numbers(item.source, item.lang)
    target_numbers = extract_numbers(item.translation, item.lang)
    source_counts = Counter(number.value for number in source_numbers)
    target_counts = Counter(number.value for number in target_numbers)
    missing = source_counts - target_counts
    extra = target_counts - source_counts
    if not missing and not extra:
        return []

    affected = list(missing.elements()) + list(extra.elements())
    evidence = {
        "source_values": [str(value) for value in source_counts.elements()],
        "target_values": [str(value) for value in target_counts.elements()],
    }
    token = next((number for number in target_numbers if number.value in extra), None)
    span = token.span if token else None
    source_token = next((number for number in source_numbers if number.value in missing), None)
    source_span = source_token.span if source_token else None
    return [Flag(
        category="number",
        severity="major" if affected else "minor",
        modality="text",
        span=span,
        source_span=source_span,
        evidence=evidence,
        detectors=["text_numbers"],
        confidence=CHECK_DEFAULTS["confidence"]["text_numbers"],
    )]
