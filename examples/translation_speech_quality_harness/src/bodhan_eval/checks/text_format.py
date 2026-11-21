import re

from ..config import CHECK_DEFAULTS
from .registry import LangStats, register_check
from ..models import Flag, Item


def _skeleton(text: str) -> tuple[list[str], list[str], bool, bool]:
    headings = re.findall(r"(?m)^#{1,6}\s+", text)
    list_items = re.findall(r"(?m)^(?:[-*+]|\d+\.)\s+", text)
    fences = text.count("```")
    return headings, list_items, fences % 2 == 0, text.count("$") % 2 == 0


@register_check(name="text_format", categories=["format"], modality="text", cost_tier=0)
def check_format(item: Item, stats: LangStats | None = None) -> list[Flag]:
    del stats
    if item.reference is None or not item.translation:
        return []
    source_skeleton = _skeleton(item.reference)
    target_skeleton = _skeleton(item.translation)
    if source_skeleton == target_skeleton:
        return []
    position = next((index for index, pair in enumerate(zip(item.reference, item.translation)) if pair[0] != pair[1]), 0)
    return [Flag(
        category="format",
        severity="major",
        modality="text",
        span=(position, min(position + 1, len(item.translation))),
        source_span=(position, min(position + 1, len(item.source))),
        evidence={"reference_skeleton": source_skeleton, "target_skeleton": target_skeleton},
        detectors=["text_format"],
        confidence=CHECK_DEFAULTS["confidence"]["text_format"],
    )]
