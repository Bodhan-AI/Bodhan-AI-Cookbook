from dataclasses import dataclass
import unicodedata


@dataclass(frozen=True)
class LangInfo:
    code: str
    script: str
    ranges: list[tuple[int, int]]
    digits_start: int
    sentence_ends: tuple[str, ...]
    default_voice: str
    asr_code: str
    rtl: bool


_LANGUAGE_TABLE: dict[str, LangInfo] = {
    "en": LangInfo("en", "Latin", [(0x0000, 0x052F)], 0, (".", "!", "?"), "en-US", "en", False),
    "hi": LangInfo("hi", "Devanagari", [(0x0900, 0x097F)], 0x0966, ("\u0964", "."), "Kavya", "hi", False),
    "bn": LangInfo("bn", "Bengali", [(0x0980, 0x09FF)], 0x09E6, ("\u0964", "."), "Ishita", "bn", False),
    "mr": LangInfo("mr", "Devanagari", [(0x0900, 0x097F)], 0x0966, ("\u0964", "."), "Anagha", "mr", False),
    "te": LangInfo("te", "Telugu", [(0x0C00, 0x0C7F)], 0x0C66, (".",), "Sravani", "te", False),
    "ta": LangInfo("ta", "Tamil", [(0x0B80, 0x0BFF)], 0x0BE6, (".",), "Anitha", "ta", False),
    "gu": LangInfo("gu", "Gujarati", [(0x0A80, 0x0AFF)], 0x0AE6, ("\u0964", "."), "Dhara", "gu", False),
    "kn": LangInfo("kn", "Kannada", [(0x0C80, 0x0CFF)], 0x0CE6, (".",), "Deepika", "kn", False),
    "ml": LangInfo("ml", "Malayalam", [(0x0D00, 0x0D7F)], 0x0D66, (".",), "Lakshmi", "ml", False),
    "or": LangInfo("or", "Odia", [(0x0B00, 0x0B7F)], 0x0B66, ("\u0964", "."), "Itishree", "or", False),
    "pa": LangInfo("pa", "Gurmukhi", [(0x0A00, 0x0A7F)], 0x0A66, ("\u0964", "."), "Kaur", "pa", False),
    "as": LangInfo("as", "Bengali", [(0x0980, 0x09FF)], 0x09E6, ("\u0964", "."), "Prastuti", "as", False),
    "ur": LangInfo(
        "ur",
        "Arabic",
        [(0x0600, 0x06FF), (0x0750, 0x077F), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF)],
        0x0660,
        ("\u06d4", "\u061f", "\u060c"),
        "Saba",
        "ur",
        True,
    ),
}

HF_DATASET_LANGUAGE_CODES = {
    "hi": "hin_Deva",
    "bn": "ben_Beng",
    "mr": "mar_Deva",
    "te": "tel_Telu",
    "ta": "tam_Taml",
    "gu": "guj_Gujr",
    "kn": "kan_Knda",
    "ml": "mal_Mlym",
    "or": "ory_Orya",
    "pa": "pan_Guru",
    "as": "asm_Beng",
    "ur": "urd_Arab",
}


def get_lang(code: str) -> LangInfo | None:
    return _LANGUAGE_TABLE.get(code)


def dominant_script(text: str) -> str:
    counts: dict[str, int] = {}
    for char in text:
        if char.isalpha():
            name = unicodedata.name(char, "")
            if "DEVANAGARI" in name:
                script = "Devanagari"
            elif "BENGALI" in name:
                script = "Bengali"
            elif "TAMIL" in name:
                script = "Tamil"
            elif "TELUGU" in name:
                script = "Telugu"
            elif "KANNADA" in name:
                script = "Kannada"
            elif "MALAYALAM" in name:
                script = "Malayalam"
            elif "GUJARATI" in name:
                script = "Gujarati"
            elif "GURMUKHI" in name:
                script = "Gurmukhi"
            elif "ORIYA" in name:
                script = "Odia"
            elif "ARABIC" in name:
                script = "Arabic"
            elif ord(char) < 128:
                script = "Latin"
            else:
                script = "Other"
            counts[script] = counts.get(script, 0) + 1
    if not counts:
        return "Unknown"
    return max(counts, key=lambda script: (counts[script], script))
