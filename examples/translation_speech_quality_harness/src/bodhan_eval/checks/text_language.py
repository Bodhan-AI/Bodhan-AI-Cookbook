from ..config import CHECK_DEFAULTS
from ..languages import dominant_script, get_lang
from ..models import Flag, Item
from .registry import LangStats, register_check


@register_check(name="text_language", categories=["language"], modality="text", cost_tier=0)
def check_language(item: Item, stats: LangStats | None = None) -> list[Flag]:
    del stats
    if not item.translation:
        return []
    info = get_lang(item.lang)
    if info is None:
        return []
    detected = dominant_script(item.translation)
    if detected not in {"Unknown", info.script}:
        if detected == "Latin" and info.script != "Latin":
            return [Flag(
                category="language",
                severity="major",
                modality="text",
                span=(0, len(item.translation)),
                evidence={"expected_script": info.script, "detected_script": detected},
                detectors=["text_language"],
                confidence=CHECK_DEFAULTS["confidence"]["text_language_latin"],
            )]
        if dominant_script(item.source) == "Latin":
            return [Flag(
                category="language",
                severity="major",
                modality="text",
                span=(0, len(item.translation)),
                evidence={"expected_script": info.script, "detected_script": detected},
                detectors=["text_language"],
                confidence=CHECK_DEFAULTS["confidence"]["text_language_ambiguous"],
            )]
    return []
