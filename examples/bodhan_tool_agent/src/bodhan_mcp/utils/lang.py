"""Language utilities, script detection, and Unicode range inspection."""

from bodhan_mcp import config

# Unicode code point ranges for Indic scripts
INDIC_SCRIPT_RANGES = {
    "Devanagari": (0x0900, 0x097F),
    "Bengali": (0x0980, 0x09FF),
    "Gurmukhi": (0x0A00, 0x0A7F),
    "Gujarati": (0x0A80, 0x0AFF),
    "Odia": (0x0B00, 0x0B7F),
    "Tamil": (0x0B80, 0x0BFF),
    "Telugu": (0x0C00, 0x0C7F),
    "Kannada": (0x0C80, 0x0CFF),
    "Malayalam": (0x0D00, 0x0D7F),
    "Arabic": (0x0600, 0x06FF),  # for Urdu
}


def detect_script(text: str) -> str:
    """Detect dominant Indic script or English/Latin in the provided text string."""
    counts: dict[str, int] = {name: 0 for name in INDIC_SCRIPT_RANGES}
    latin_count = 0

    for char in text:
        cp = ord(char)
        # Latin
        if (0x0041 <= cp <= 0x005A) or (0x0061 <= cp <= 0x007A):
            latin_count += 1
            continue

        for script_name, (start, end) in INDIC_SCRIPT_RANGES.items():
            if start <= cp <= end:
                counts[script_name] += 1
                break

    best_script, max_count = max(counts.items(), key=lambda item: item[1])
    if max_count > 0 and max_count >= latin_count:
        return best_script
    if latin_count > 0:
        return "Latin"
    return "Unknown"


def contains_indic_unicode(text: str) -> bool:
    """Check if text contains any characters from Indic Unicode ranges (Constraint C2)."""
    for char in text:
        cp = ord(char)
        for start, end in INDIC_SCRIPT_RANGES.values():
            if start <= cp <= end:
                return True
    return False


def is_supported_language(lang: str, model_type: str) -> bool:
    """Verify if a language code is supported by the given model type."""
    supported = config.SUPPORTED_LANGS.get(model_type, [])
    return lang.lower().strip() in supported
