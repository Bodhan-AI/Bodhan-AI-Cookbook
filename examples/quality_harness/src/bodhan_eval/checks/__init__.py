"""Deterministic check plugins. Importing this package registers every check via the
`@register` decorator so the pipeline can discover them. Add a new check by dropping a
file here and decorating its function -- no other wiring needed.
"""

from __future__ import annotations

from . import (  # noqa: F401  (import for side-effect registration)
    t1_script_language,
    t2_number_unit,
    t3_format,
    t4_length_ratio,
    t5_reference_chrf,
    t6_term_entity,
    t7_sentence_coverage,
)
from .registry import CheckContext, all_checks, register  # noqa: F401
