import re
import unicodedata


def normalize_text(text: str) -> str:
    """Normalize Unicode, decimal digits, and whitespace."""
    if not isinstance(text, str):
        raise TypeError("Expected text to be a string")

    text = unicodedata.normalize("NFC", text)

    # Convert Unicode decimal digits from different scripts to 0-9.
    normalized_chars = []
    for char in text:
        try:
            normalized_chars.append(str(unicodedata.decimal(char)))
        except (TypeError, ValueError):
            normalized_chars.append(char)

    text = "".join(normalized_chars)
    text = re.sub(r"\s+", " ", text)

    return text.strip()
