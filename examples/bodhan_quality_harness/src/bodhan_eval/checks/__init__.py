"""Check registry.

Tiers encode cost. The runner always executes tier 0-1 (free, deterministic),
and only reaches tier 2 (judge) and tier 3 (paid audio round-trip) when the item
justifies it. That ordering is the core of the harness design.
"""

from __future__ import annotations

from . import format as format_checks
from . import length as length_checks
from . import numbers as number_checks
from . import reference as reference_checks
from . import script as script_checks
from . import terms as term_checks
from .base import CATEGORIES, SEVERITIES, SEVERITY_WEIGHT, Flag, ItemContext, Registry

__all__ = [
    "CATEGORIES", "SEVERITIES", "SEVERITY_WEIGHT", "Flag", "ItemContext",
    "Registry", "build_registry",
]


def build_registry(thresholds) -> Registry:
    """Free checks only. Paid stages are driven by the runner, not the registry."""
    reg = Registry()
    # tier 0: cheapest, highest precision
    reg.add("script.wrong_script", 0, script_checks.check_script)
    reg.add("script.untranslated", 0, script_checks.check_untranslated)
    reg.add("numbers", 0, number_checks.check_numbers)
    # check_units is deliberately not registered: on real Indic-Translate output a
    # unit is rendered as a word ("100 °C" -> "100 डिग्री सेल्सियस"), which is correct,
    # not a lost unit. numbers.missing already covers a quantity that disappears
    # entirely. Keeping it cost 4 false alarms on 57 clean items for no recall gain.
    reg.add("format", 0, format_checks.check_format)

    # tier 1: cheap, noisier — these are the ones calibration tunes
    reg.add("terms.entities", 1, term_checks.check_terms)
    reg.add("terms.glossary", 1, term_checks.check_glossary)
    reg.add("length", 1, lambda ctx: length_checks.check_length(
        ctx, lo=thresholds.length_lo, hi=thresholds.length_hi))
    reg.add("length.repeat", 1, length_checks.check_repeats)
    reg.add("reference.chrf", 1, reference_checks.check_reference)
    reg.add("script.latin_ratio", 1, script_checks.check_latin_ratio)
    return reg
