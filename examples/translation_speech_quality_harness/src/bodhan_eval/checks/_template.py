from ..models import Flag, Item
from .registry import LangStats, register_check


@register_check(name="toy", categories=["mistranslation"], modality="text", cost_tier=0)
def check_toy(item: Item, stats: LangStats) -> list[Flag]:
    del stats
    if "replace-this-with-your-signal" in (item.translation or ""):
        return [Flag(
            category="mistranslation",
            severity="minor",
            modality="text",
            span=None,
            evidence={"signal": "toy"},
            detectors=["toy"],
            confidence=0.5,
        )]
    return []
