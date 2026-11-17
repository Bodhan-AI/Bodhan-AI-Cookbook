"""
guard.py
--------
Detects Indic script characters in LLM-produced text and raises an error
when the LLM appears to have generated Indian-language content itself.

Rule: ALL Indic text MUST come from Bodhan tools, never from the LLM.
"""

import re

# ---------------------------------------------------------------------------
# Unicode ranges for major Indic scripts used in India
# ---------------------------------------------------------------------------
INDIC_PATTERN = re.compile(
    r"["
    r"\u0900-\u097F"   # Devanagari  (Hindi, Marathi, Sanskrit, Konkani, …)
    r"\u0980-\u09FF"   # Bengali / Assamese
    r"\u0A00-\u0A7F"   # Gurmukhi   (Punjabi)
    r"\u0A80-\u0AFF"   # Gujarati
    r"\u0B00-\u0B7F"   # Odia
    r"\u0B80-\u0BFF"   # Tamil
    r"\u0C00-\u0C7F"   # Telugu
    r"\u0C80-\u0CFF"   # Kannada
    r"\u0D00-\u0D7F"   # Malayalam
    r"\u0D80-\u0DFF"   # Sinhala
    r"\u1C50-\u1C7F"   # Ol Chiki   (Santali)
    r"\u1CD0-\u1CFF"   # Vedic Extensions
    r"\uABC0-\uABFF"   # Meetei Mayek
    r"\U00011480-\U000114DF"  # Tirhuta    (Maithili)
    r"]"
)

# Default allowance: a handful of Indic characters may appear in proper nouns,
# file names, or technical strings embedded in English context.
_DEFAULT_ALLOWANCE: int = 10


def check(text: str, allowance: int = _DEFAULT_ALLOWANCE) -> tuple[bool, int]:
    """
    Scan *text* for Indic script characters.

    Returns
    -------
    (passes, indic_char_count)
        ``passes`` is True when the number of Indic characters is within
        *allowance* (i.e. the text is considered safe for LLM-generated content).
    """
    matches = INDIC_PATTERN.findall(text)
    count = len(matches)
    return (count <= allowance, count)


def assert_safe(text: str, context: str = "") -> None:
    """
    Raise ``ValueError`` if *text* contains more Indic characters than the
    default allowance.

    Parameters
    ----------
    text:
        The LLM-produced string to validate.
    context:
        A short label used in the error message (e.g. ``"summarize"``).

    Raises
    ------
    ValueError
        When the Indic character count exceeds the allowance, indicating the
        LLM has generated Indian-language content itself instead of routing
        through the appropriate Bodhan tool.
    """
    ok, count = check(text)
    if not ok:
        ctx_suffix = f" [{context}]" if context else ""
        raise ValueError(
            f"Script guard: LLM output contains {count} Indic characters{ctx_suffix}. "
            "Route Indian-language text through translate_text or another Bodhan tool "
            "instead of generating it directly."
        )
