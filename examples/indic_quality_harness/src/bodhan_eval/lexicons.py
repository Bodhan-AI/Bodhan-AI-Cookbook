"""Small, explicit lexicons used by the deterministic checks.

These lists are deliberately short and hand-curated for the languages the
harness ships with (English source, Hindi / Tamil / Bengali targets). They are
not exhaustive. A miss produces a ``review`` status, never a silent pass, and
only *contradicting* evidence (for example a different unit of the same
dimension) produces a hard failure.

Every entry is NFC-normalised at import time so that lookups work no matter
how the text was composed (for example Bengali ``য়`` or Hindi ``ड़``, which
NFC decomposes).
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _nfc_map(mapping: dict) -> dict:
    return {
        _nfc(key) if isinstance(key, str) else key: (
            [_nfc(v) for v in value] if isinstance(value, list) else value
        )
        for key, value in mapping.items()
    }


LANGUAGE_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "ta": "Tamil",
    "bn": "Bengali",
    "te": "Telugu",
    "mr": "Marathi",
    "gu": "Gujarati",
    "kn": "Kannada",
    "ml": "Malayalam",
    "pa": "Punjabi",
    "or": "Odia",
    "as": "Assamese",
    "ur": "Urdu",
}

# Default Indic-Speak voice per language (male voices from the Bodhan API
# reference). "Amit" was the historical default for every language.
DEFAULT_VOICES = {
    "hi": "Amit",
    "ta": "Arun",
    "bn": "Sourav",
    "te": "Vamsi",
    "mr": "Chinmay",
    "gu": "Parth",
    "kn": "Adarsh",
    "ml": "Kiran",
    "pa": "Manpreet",
    "or": "Akash",
    "as": "Ankur",
    "ur": "Zaid",
}


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------

# Spelled-out cardinal numbers. Used only to downgrade a number mismatch from
# "fail" to "review" (the number may be written as a word), never to pass it.
NUMBER_WORDS: dict[str, dict[str, str]] = {
    "en": {
        "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
        "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
        "ten": "10", "eleven": "11", "twelve": "12", "fifteen": "15",
        "twenty": "20", "thirty": "30", "fifty": "50", "hundred": "100",
    },
    "hi": _nfc_map({
        "शून्य": "0", "एक": "1", "दो": "2", "तीन": "3", "चार": "4",
        "पांच": "5", "पाँच": "5", "छह": "6", "छः": "6", "सात": "7",
        "आठ": "8", "नौ": "9", "दस": "10", "ग्यारह": "11", "बारह": "12",
        "पंद्रह": "15", "पन्द्रह": "15", "बीस": "20", "तीस": "30",
        "पचास": "50", "सौ": "100",
    }),
    "bn": _nfc_map({
        "শূন্য": "0", "এক": "1", "দুই": "2", "তিন": "3", "চার": "4",
        "পাঁচ": "5", "ছয়": "6", "সাত": "7", "আট": "8", "নয়": "9",
        "দশ": "10", "এগারো": "11", "বারো": "12", "পনেরো": "15",
        "বিশ": "20", "কুড়ি": "20", "ত্রিশ": "30", "পঞ্চাশ": "50",
        "একশো": "100", "একশ": "100",
    }),
    "ta": _nfc_map({
        "பூஜ்ஜியம்": "0", "ஒன்று": "1", "ஒரு": "1", "இரண்டு": "2",
        "மூன்று": "3", "நான்கு": "4", "ஐந்து": "5", "ஆறு": "6",
        "ஏழு": "7", "எட்டு": "8", "ஒன்பது": "9", "பத்து": "10",
        "பதினொன்று": "11", "பன்னிரண்டு": "12", "பதினைந்து": "15",
        "இருபது": "20", "முப்பது": "30", "ஐம்பது": "50", "நூறு": "100",
    }),
}

# Scale words that multiply the number immediately before them. Matching is by
# prefix so that inflected Tamil forms (``லட்சத்துக்கும்``) still match.
SCALE_WORDS: dict[str, Decimal] = _nfc_map({
    "thousand": Decimal(10**3),
    "lakh": Decimal(10**5),
    "crore": Decimal(10**7),
    "million": Decimal(10**6),
    "billion": Decimal(10**9),
    # Hindi
    "हज़ार": Decimal(10**3), "हजार": Decimal(10**3),
    "लाख": Decimal(10**5), "करोड़": Decimal(10**7),
    "मिलियन": Decimal(10**6), "बिलियन": Decimal(10**9), "अरब": Decimal(10**9),
    # Bengali
    "হাজার": Decimal(10**3), "লক্ষ": Decimal(10**5), "লাখ": Decimal(10**5),
    "কোটি": Decimal(10**7), "মিলিয়ন": Decimal(10**6),
    "বিলিয়ন": Decimal(10**9),
    # Tamil (prefixes)
    "ஆயிர": Decimal(10**3), "லட்ச": Decimal(10**5), "இலட்ச": Decimal(10**5),
    "கோடி": Decimal(10**7), "மில்லியன்": Decimal(10**6),
    "பில்லியன்": Decimal(10**9),
})


# ---------------------------------------------------------------------------
# Units and other "concept" lexicons
#
# Each concept has a group (dimension) and aliases per language. English
# aliases are always accepted in the target text as well, because units such
# as "MB" or "km" are frequently kept in Latin script.
# ---------------------------------------------------------------------------

UNITS: dict[str, dict] = {
    "km": {"group": "length", "en": ["km", "kms", "kilometer", "kilometers", "kilometre", "kilometres"],
           "hi": ["किलोमीटर", "किमी", "कि.मी."], "bn": ["কিলোমিটার", "কিমি"], "ta": ["கிலோமீட்டர்", "கி.மீ"]},
    "m": {"group": "length", "en": ["meter", "meters", "metre", "metres"],
          "hi": ["मीटर"], "bn": ["মিটার"], "ta": ["மீட்டர்"]},
    "cm": {"group": "length", "en": ["cm", "centimeter", "centimeters", "centimetre", "centimetres"],
           "hi": ["सेंटीमीटर", "सेमी"], "bn": ["সেন্টিমিটার"], "ta": ["சென்டிமீட்டர்"]},
    "mm": {"group": "length", "en": ["mm", "millimeter", "millimeters", "millimetre", "millimetres"],
           "hi": ["मिलीमीटर", "मिमी"], "bn": ["মিলিমিটার", "মিমি"], "ta": ["மில்லிமீட்டர்", "மி.மீ"]},
    "kg": {"group": "mass", "en": ["kg", "kgs", "kilogram", "kilograms", "kilo", "kilos"],
           "hi": ["किलोग्राम", "किलो"], "bn": ["কিলোগ্রাম", "কেজি"], "ta": ["கிலோகிராம்", "கிலோ"]},
    "g": {"group": "mass", "en": ["gram", "grams", "gramme", "grammes"],
          "hi": ["ग्राम"], "bn": ["গ্রাম"], "ta": ["கிராம்"]},
    "gb": {"group": "data", "en": ["GB", "gigabyte", "gigabytes"],
           "hi": ["जीबी", "गीगाबाइट"], "bn": ["জিবি", "গিগাবাইট"], "ta": ["ஜிபி", "ஜிகாபைட்"]},
    "mb": {"group": "data", "en": ["MB", "megabyte", "megabytes"],
           "hi": ["एमबी", "मेगाबाइट"], "bn": ["এমবি", "মেগাবাইট"], "ta": ["எம்பி", "மெகாபைட்"]},
    "mbps": {"group": "bitrate", "en": ["Mbps"],
             "hi": ["एमबीपीएस"], "bn": ["এমবিপিএস"], "ta": ["எம்பிபிஎஸ்"]},
    "second": {"group": "time", "en": ["second", "seconds", "sec", "secs"],
               "hi": ["सेकंड", "सेकेंड", "सेकण्ड"], "bn": ["সেকেন্ড"], "ta": ["வினாடி", "விநாடி"]},
    "minute": {"group": "time", "en": ["minute", "minutes", "min", "mins"],
               "hi": ["मिनट"], "bn": ["মিনিট"], "ta": ["நிமிட"]},
    "hour": {"group": "time", "en": ["hour", "hours", "hr", "hrs"],
             "hi": ["घंटे", "घंटा", "घण्टे", "घण्टा"], "bn": ["ঘণ্টা", "ঘন্টা"], "ta": ["மணி நேர", "மணிநேர"]},
    "day": {"group": "time", "en": ["day", "days"],
            "hi": ["दिन"], "bn": ["দিন"], "ta": ["நாள்", "நாட்க", "நாட்கள்"]},
    "week": {"group": "time", "en": ["week", "weeks"],
             "hi": ["सप्ताह", "हफ्ते", "हफ़्ते", "हफ्ता"], "bn": ["সপ্তাহ"], "ta": ["வாரம்", "வாரத்"]},
    "month": {"group": "time", "en": ["month", "months"],
              "hi": ["महीने", "महीना", "माह"], "bn": ["মাস"], "ta": ["மாதம்", "மாதங்க", "மாதத்"]},
    "year": {"group": "time", "en": ["year", "years"],
             "hi": ["साल", "वर्ष"], "bn": ["বছর", "বৎসর"], "ta": ["ஆண்டு", "வருட"]},
    "percent": {"group": "ratio", "en": ["%", "percent", "per cent"],
                "hi": ["प्रतिशत", "फीसदी", "फ़ीसदी"], "bn": ["শতাংশ"], "ta": ["சதவீத", "சதவிகித"]},
    "degree": {"group": "temperature", "en": ["degree", "degrees", "°"],
               "hi": ["डिग्री"], "bn": ["ডিগ্রি"], "ta": ["டிகிரி"]},
    "rupee": {"group": "currency", "en": ["rupee", "rupees", "Rs", "Rs.", "INR", "₹"],
              "hi": ["रुपये", "रुपए", "रुपया", "रु."], "bn": ["টাকা", "রুপি"], "ta": ["ரூபாய்", "ரூ."]},
    "dollar": {"group": "currency", "en": ["dollar", "dollars", "USD"],
               "hi": ["डॉलर"], "bn": ["ডলার"], "ta": ["டாலர்"]},
}

MONTHS: dict[str, dict] = {
    "january": {"group": "month", "en": ["January", "Jan"], "hi": ["जनवरी"], "bn": ["জানুয়ারি", "জানুয়ারী"], "ta": ["ஜனவரி"]},
    "february": {"group": "month", "en": ["February", "Feb"], "hi": ["फरवरी", "फ़रवरी"], "bn": ["ফেব্রুয়ারি", "ফেব্রুয়ারী"], "ta": ["பிப்ரவரி"]},
    "march": {"group": "month", "en": ["March"], "hi": ["मार्च"], "bn": ["মার্চ"], "ta": ["மார்ச்"]},
    "april": {"group": "month", "en": ["April", "Apr"], "hi": ["अप्रैल"], "bn": ["এপ্রিল"], "ta": ["ஏப்ரல்"]},
    "may": {"group": "month", "en": ["May"], "hi": ["मई"], "bn": ["মে"], "ta": ["மே"]},
    "june": {"group": "month", "en": ["June"], "hi": ["जून"], "bn": ["জুন"], "ta": ["ஜூன்"]},
    "july": {"group": "month", "en": ["July"], "hi": ["जुलाई"], "bn": ["জুলাই"], "ta": ["ஜூலை"]},
    "august": {"group": "month", "en": ["August", "Aug"], "hi": ["अगस्त"], "bn": ["আগস্ট", "অগাস্ট"], "ta": ["ஆகஸ்ட்"]},
    "september": {"group": "month", "en": ["September", "Sept", "Sep"], "hi": ["सितंबर", "सितम्बर"], "bn": ["সেপ্টেম্বর"], "ta": ["செப்டம்பர்"]},
    "october": {"group": "month", "en": ["October", "Oct"], "hi": ["अक्टूबर", "अक्तूबर"], "bn": ["অক্টোবর"], "ta": ["அக்டோபர்"]},
    "november": {"group": "month", "en": ["November", "Nov"], "hi": ["नवंबर", "नवम्बर"], "bn": ["নভেম্বর"], "ta": ["நவம்பர்"]},
    "december": {"group": "month", "en": ["December", "Dec"], "hi": ["दिसंबर", "दिसम्बर"], "bn": ["ডিসেম্বর"], "ta": ["டிசம்பர்"]},
}

WEEKDAYS: dict[str, dict] = {
    "monday": {"group": "weekday", "en": ["Monday"], "hi": ["सोमवार"], "bn": ["সোমবার"], "ta": ["திங்கள்", "திங்கட்கிழமை"]},
    "tuesday": {"group": "weekday", "en": ["Tuesday"], "hi": ["मंगलवार"], "bn": ["মঙ্গলবার"], "ta": ["செவ்வாய்"]},
    "wednesday": {"group": "weekday", "en": ["Wednesday"], "hi": ["बुधवार"], "bn": ["বুধবার"], "ta": ["புதன்"]},
    "thursday": {"group": "weekday", "en": ["Thursday"], "hi": ["गुरुवार", "बृहस्पतिवार"], "bn": ["বৃহস্পতিবার"], "ta": ["வியாழன்", "வியாழக்கிழமை"]},
    "friday": {"group": "weekday", "en": ["Friday"], "hi": ["शुक्रवार"], "bn": ["শুক্রবার"], "ta": ["வெள்ளி"]},
    "saturday": {"group": "weekday", "en": ["Saturday"], "hi": ["शनिवार"], "bn": ["শনিবার"], "ta": ["சனிக்கிழமை", "சனி"]},
    "sunday": {"group": "weekday", "en": ["Sunday"], "hi": ["रविवार"], "bn": ["রবিবার"], "ta": ["ஞாயிறு", "ஞாயிற்று"]},
}

DAY_PERIODS: dict[str, dict] = {
    "am": {"group": "day_period", "en": ["AM", "a.m."], "hi": ["सुबह", "प्रातः", "पूर्वाह्न"],
           "bn": ["সকাল", "ভোর", "পূর্বাহ্ণ"], "ta": ["காலை"]},
    "pm": {"group": "day_period", "en": ["PM", "p.m."], "hi": ["शाम", "दोपहर", "रात", "अपराह्न", "सायं"],
           "bn": ["বিকেল", "সন্ধ্যা", "রাত", "দুপুর", "অপরাহ্ণ"], "ta": ["மாலை", "மதியம்", "இரவு", "பிற்பகல்"]},
}

PLACES: dict[str, dict] = {
    "delhi": {"group": "place", "en": ["New Delhi", "Delhi"], "hi": ["दिल्ली"], "bn": ["দিল্লি", "দিল্লী"], "ta": ["டெல்லி", "தில்லி"]},
    "mumbai": {"group": "place", "en": ["Mumbai", "Bombay"], "hi": ["मुंबई", "मुम्बई"], "bn": ["মুম্বাই", "মুম্বই"], "ta": ["மும்பை"]},
    "chennai": {"group": "place", "en": ["Chennai", "Madras"], "hi": ["चेन्नई"], "bn": ["চেন্নাই"], "ta": ["சென்னை"]},
    "kolkata": {"group": "place", "en": ["Kolkata", "Calcutta"], "hi": ["कोलकाता"], "bn": ["কলকাতা"], "ta": ["கொல்கத்தா"]},
    "bengaluru": {"group": "place", "en": ["Bengaluru", "Bangalore"], "hi": ["बेंगलुरु", "बैंगलोर"], "bn": ["বেঙ্গালুরু"], "ta": ["பெங்களூரு"]},
    "india": {"group": "place", "en": ["India"], "hi": ["भारत"], "bn": ["ভারত"], "ta": ["இந்திய"]},
}


def _normalise_concepts(concepts: dict[str, dict]) -> dict[str, dict]:
    return {
        concept_id: {
            key: [_nfc(alias) for alias in value] if isinstance(value, list) else value
            for key, value in spec.items()
        }
        for concept_id, spec in concepts.items()
    }


UNITS = _normalise_concepts(UNITS)
MONTHS = _normalise_concepts(MONTHS)
WEEKDAYS = _normalise_concepts(WEEKDAYS)
DAY_PERIODS = _normalise_concepts(DAY_PERIODS)
PLACES = _normalise_concepts(PLACES)

# Very short Indic aliases (for example Tamil "மே" or Bengali "মে") would
# match inside unrelated words, so aliases of this length or less must match a
# whole token.
_MIN_SUBSTRING_ALIAS = 3


def _alias_pattern(alias: str) -> re.Pattern:
    """Build a regex for an alias.

    Latin aliases use word boundaries. Lower-case aliases ("kilometers") are
    case-insensitive; aliases containing capitals ("May", "MB", "AM") are
    case-sensitive, so the verb "may" is not read as the month May.
    Indic aliases are substring matches so that agglutinative suffixes still
    match, except very short ones which must match a whole token.
    """
    escaped = re.escape(alias)
    is_latin = all(ord(ch) < 0x250 or not ch.isalpha() for ch in alias)

    if is_latin:
        flags = re.IGNORECASE if alias == alias.lower() else 0
        # Symbols such as "%" or "°" have no word boundary of their own.
        if alias[0].isalnum():
            escaped = r"(?<![\w])" + escaped
        if alias[-1].isalnum():
            escaped = escaped + r"(?![\w])"
        return re.compile(escaped, flags)

    if len(alias) <= _MIN_SUBSTRING_ALIAS:
        # Indic combining marks are not \w, so include the Indic blocks.
        boundary = r"\wऀ-෿"
        return re.compile(rf"(?<![{boundary}])" + escaped + rf"(?![{boundary}])")

    return re.compile(escaped)


def find_concepts(
    text: str,
    concepts: dict[str, dict],
    language: str | None = None,
) -> list[str]:
    """Return the concept ids found in ``text`` (with repetitions, in order).

    Aliases are matched longest-first and matched characters are consumed, so
    "किलोमीटर" counts as km and not also as metre.
    """
    return [concept_id for _, _, concept_id in find_concept_spans(text, concepts, language)]


def find_concept_spans(
    text: str,
    concepts: dict[str, dict],
    language: str | None = None,
) -> list[tuple[int, int, str]]:
    """Like :func:`find_concepts` but returns ``(start, end, concept_id)``."""
    text = _nfc(text)
    languages = ["en"] + ([language] if language and language != "en" else [])
    if language is None:
        languages = sorted({key for spec in concepts.values() for key in spec if key != "group"})

    aliases = []
    for concept_id, spec in concepts.items():
        for lang in languages:
            for alias in spec.get(lang, []):
                aliases.append((alias, concept_id))

    aliases.sort(key=lambda item: len(item[0]), reverse=True)

    consumed = [False] * len(text)
    found: list[tuple[int, int, str]] = []

    for alias, concept_id in aliases:
        for match in _alias_pattern(alias).finditer(text):
            start, end = match.span()
            if any(consumed[start:end]):
                continue
            for index in range(start, end):
                consumed[index] = True
            found.append((start, end, concept_id))

    return sorted(found)


def concept_group(concepts: dict[str, dict], concept_id: str) -> str:
    return concepts[concept_id]["group"]


def aliases_for(concepts: dict[str, dict], concept_id: str, language: str) -> list[str]:
    return list(concepts[concept_id].get(language, []))
