"""T1 script_language: is the translation actually in the expected script/language?

Flags wrong script, untranslated text (output == source / all Latin) as STRONG evidence,
because these are unambiguous and deterministic. Latin runs for numbers, units, acronyms,
URLs, code and LaTeX are excluded before measuring the script fraction, so a Hindi sentence
that legitimately contains "DNA" or "5 km" is not penalized.
"""

from __future__ import annotations

import re

from ..models import Evidence
from ..normalize import norm_for_compare
from .registry import CheckContext, register

_LATEX_OR_CODE = re.compile(r"\$\$.*?\$\$|\$[^$]*\$|`[^`]*`|```.*?```|https?://\S+", re.DOTALL)
_LATIN_RUN = re.compile(r"[A-Za-z]{2,}")


def _expected_fraction(ctx: CheckContext, text: str) -> tuple[float, int]:
    """Fraction of 'scriptable' letters that fall in the expected block, and the count of
    scriptable letters considered."""
    script = ctx.languages.script_for(ctx.lang)
    stripped = _LATEX_OR_CODE.sub(" ", text)
    letters = [c for c in stripped if c.isalpha()]
    if not letters:
        return 1.0, 0  # nothing to judge (pure numbers/symbols) -> don't flag
    if script is None:
        # unknown language: accept any non-Latin letters as "expected"
        non_latin = sum(1 for c in letters if not c.isascii())
        return non_latin / len(letters), len(letters)
    in_block = sum(1 for c in letters if script.contains(ord(c)))
    return in_block / len(letters), len(letters)


@register("t1_script_language", category="wrong_language_script",
          description="expected-script fraction; untranslated / wrong-script detection")
def check(ctx: CheckContext) -> list[Evidence]:
    tr = ctx.translation
    if not tr or not tr.strip():
        return []
    out: list[Evidence] = []

    # untranslated: translation identical to source after normalization
    if norm_for_compare(tr, ctx.lang) == norm_for_compare(ctx.source, ctx.lang) and ctx.source.strip():
        words = re.findall(r"[A-Za-z]+", _LATEX_OR_CODE.sub(" ", ctx.source))
        if len(words) < 2 and (not words or words[0].isupper() or words[0][:1].isupper()):
            return []  # numbers, formulas, acronyms and standalone proper names can be unchanged
        out.append(Evidence(
            check="t1_script_language", category="wrong_language_script", strength="strong",
            noisy=False, score=1.0, severity_hint="critical",
            target_span=(0, len(tr)),
            details={"reason": "translation identical to source (untranslated)"},
        ))
        return out

    frac, n = _expected_fraction(ctx, tr)
    cut = ctx.thresholds.get("script_min_fraction", ctx.lang)
    if n >= 3 and frac < cut:
        # is it mostly another Indic script, or mostly Latin (left in English)?
        mostly_latin = bool(_LATIN_RUN.search(_LATEX_OR_CODE.sub(" ", tr))) and frac < 0.2
        out.append(Evidence(
            check="t1_script_language", category="wrong_language_script", strength="strong",
            noisy=False, score=1.0 - frac, severity_hint="critical",
            target_span=(0, len(tr)),
            details={
                "expected_script_fraction": round(frac, 3),
                "threshold": cut,
                "scriptable_letters": n,
                "likely": "left_in_english" if mostly_latin else "wrong_script",
            },
        ))
    return out
