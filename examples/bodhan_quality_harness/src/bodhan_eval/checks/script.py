"""Language and script checks.

Cheap, deterministic, and they catch two of the seeded categories outright:
output in the wrong language, and text left untranslated.
"""

from __future__ import annotations

import re

from ..normalize import (
    expected_script, normalize, script_conformance, script_profile, word_count,
)
from .base import Flag, ItemContext


def check_script(ctx: ItemContext) -> list[Flag]:
    if not ctx.translation:
        return []
    want = expected_script(ctx.lang)
    if want is None:
        return []

    prose = STRUCTURAL.sub(" ", ctx.translation)
    if len(prose.split()) < 4:
        prose = ctx.translation
    frac, detected = script_conformance(prose, ctx.lang)
    if frac < 0.35:
        # Almost none of the output is in the language's own script.
        return [Flag(
            category="language", severity="critical", span=(0, min(len(ctx.translation), 80)),
            evidence=f"expected {want} script for '{ctx.lang}' but the output is "
                     f"{detected or 'Latin/other'} ({frac:.0%} in-script)",
            check="script.wrong_script", confidence=0.95,
        )]
    if frac < 0.75:
        return [Flag(
            category="language", severity="minor", span=(0, min(len(ctx.translation), 80)),
            evidence=f"mixed script: only {frac:.0%} of characters are {want}",
            check="script.mixed_script", confidence=0.6,
        )]
    return []


def check_untranslated(ctx: ItemContext) -> list[Flag]:
    """Source and output identical (or near) when a translation was expected."""
    if not ctx.translation:
        return []
    if (ctx.lang or "").lower().startswith("en"):
        return []
    if normalize(ctx.source).lower() == normalize(ctx.translation).lower():
        return [Flag(
            category="language", severity="critical", span=(0, min(len(ctx.translation), 80)),
            evidence="the translation is identical to the English source (left untranslated)",
            check="script.untranslated", confidence=0.95,
        )]
    return []


STRUCTURAL = re.compile(r"```.*?```|\$[^$]*\$|\|.*?\|", re.DOTALL)


def check_latin_ratio(ctx: ItemContext) -> list[Flag]:
    """Too much Latin text where the source had almost none.

    A weak signal on its own — product names are legitimately Latin — so it is
    reported at low confidence and only when the imbalance is stark.
    """
    if not ctx.translation:
        return []
    # Tables, code fences and LaTeX legitimately carry Latin text. Strip them
    # before judging, otherwise every Markdown item becomes a false alarm.
    prose = STRUCTURAL.sub(" ", ctx.translation)
    if len(prose.split()) < 6:
        return []
    prof = script_profile(prose)
    in_script = sum(prof.values())
    if in_script == 0:
        return []  # handled by check_script
    latin = sum(1 for c in prose if c.isascii() and c.isalpha())
    src_latin = sum(1 for c in ctx.source if c.isascii() and c.isalpha())
    src_prof = script_profile(ctx.source)
    # If the source was already Latin-heavy the output legitimately is too.
    if src_latin and not sum(src_prof.values()):
        return []
    if latin > 0 and latin / (latin + in_script) > 0.55 and word_count(prose) > 6:
        return [Flag(
            category="language", severity="major", span=None,
            evidence=f"{latin} Latin letters vs {in_script} in-script characters — "
                     f"possibly partially untranslated",
            check="script.latin_ratio", confidence=0.4,
        )]
    return []
