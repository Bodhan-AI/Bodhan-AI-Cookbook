from ..models import Flag, Item
from .registry import CHECK_REGISTRY, LangStats, registered_checks, register_check
from .text_format import check_format
from .text_language import check_language
from .text_length import check_length
from .text_numbers import check_numbers

__all__ = [
    "CHECK_REGISTRY",
    "LangStats",
    "check_format",
    "check_language",
    "check_length",
    "check_numbers",
    "registered_checks",
    "register_check",
    "run_text_checks",
]


def run_text_checks(item: Item, stats: LangStats) -> list[Flag]:
    flags: list[Flag] = []
    for spec in registered_checks("text"):
        flags.extend(spec.runner(item, stats))
    return flags
