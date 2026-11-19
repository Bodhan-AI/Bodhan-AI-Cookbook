"""Classify autodetected transcripts using letter scripts as evidence."""

import unicodedata

AUDIO_SUFFIXES = {
    ".aac", ".aif", ".aiff", ".alac", ".amr", ".flac", ".m4a", ".m4b", ".mp3", ".mp4",
    ".oga", ".ogg", ".opus", ".wav", ".wave", ".weba", ".webm", ".wma",
}

# Scripts that the supported languages can be written in. A shared script alone
# cannot distinguish the individual spoken languages in its group.
SCRIPT_LANGUAGES = {
    "ARABIC": {"ks", "sd", "ur"},
    "BENGALI": {"as", "bn", "mni"},
    "DEVANAGARI": {"bhb", "bho", "brx", "doi", "hi", "kok", "ks", "mai", "mr",
                   "ne", "sa", "sat", "sd"},
    "GUJARATI": {"gu", "bhb"},
    "GURMUKHI": {"pa"},
    "KANNADA": {"kn", "kok"},
    "LATIN": {"en", "kok", "mni", "sat"},
    "MALAYALAM": {"ml"},
    "MEETEI": {"mni"},
    "OL": {"sat"},
    "ORIYA": {"or", "sat"},
    "TAMIL": {"ta"},
    "TELUGU": {"te"},
}
MIN_LETTERS = 4


def assess_transcript(text: str, candidate: str) -> tuple[str, list[str], str]:
    letters = [char for char in text if unicodedata.category(char).startswith("L")]
    scripts = sorted({unicodedata.name(char, "UNKNOWN").split()[0] for char in letters})
    if len(scripts) > 1:
        return "mixed", scripts, "The transcript contains letters from multiple scripts."
    if len(letters) < MIN_LETTERS:
        return "unknown", scripts, "The transcript has too little letter evidence."
    possibilities = SCRIPT_LANGUAGES.get(scripts[0])
    if not possibilities:
        return "unknown", scripts, "The transcript has no recognized script evidence."
    if possibilities == {candidate}:
        return "match", scripts, "The script points to the requested language among supported languages."
    if candidate in possibilities or scripts[0] == "LATIN":
        return "unknown", scripts, "The script does not distinguish the spoken language."
    return "non_match", scripts, "The script points away from the requested language."
