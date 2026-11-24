"""Importing this package registers every check."""
from . import backtranslation, entities, format_check, length, numbers, script_lang, speech  # noqa: F401
from .base import REGISTRY, Check, Context, Signal, register  # noqa: F401
