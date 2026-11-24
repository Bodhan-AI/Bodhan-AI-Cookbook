"""Static language, voice and price tables (mirrors the Cookbook's bodhan_api_rules.json)."""
from __future__ import annotations

LANGUAGE_NAMES: dict[str, str] = {'as': 'Assamese',
 'bhb': 'Bhili',
 'bho': 'Bhojpuri',
 'bn': 'Bengali',
 'brx': 'Bodo',
 'doi': 'Dogri',
 'en': 'English',
 'gu': 'Gujarati',
 'hi': 'Hindi',
 'kn': 'Kannada',
 'kok': 'Konkani',
 'ks': 'Kashmiri (Perso-Arabic)',
 'ks-Deva': 'Kashmiri (Devanagari)',
 'mai': 'Maithili',
 'ml': 'Malayalam',
 'mni': 'Manipuri (Meitei Mayek)',
 'mni-Beng': 'Manipuri (Bengali script)',
 'mr': 'Marathi',
 'ne': 'Nepali',
 'or': 'Odia',
 'pa': 'Punjabi',
 'sa': 'Sanskrit',
 'sat': 'Santali',
 'sd': 'Sindhi (Devanagari)',
 'sd-Arab': 'Sindhi (Perso-Arabic)',
 'ta': 'Tamil',
 'te': 'Telugu',
 'ur': 'Urdu'}

ASR_LANGUAGES: tuple[str, ...] = ('as', 'bhb', 'bho', 'bn', 'brx', 'doi', 'en', 'gu', 'hi', 'kn', 'kok', 'ks', 'mai', 'ml', 'mni', 'mr', 'ne', 'or', 'pa', 'sa', 'sat', 'sd', 'ta', 'te', 'ur')
MT_LANGUAGES: tuple[str, ...] = ('as', 'bn', 'brx', 'doi', 'en', 'gu', 'hi', 'kn', 'kok', 'ks', 'ks-Deva', 'mai', 'ml', 'mni', 'mni-Beng', 'mr', 'ne', 'or', 'pa', 'sa', 'sat', 'sd', 'sd-Arab', 'ta', 'te', 'ur')
TTS_LANGUAGES: tuple[str, ...] = ('as', 'bn', 'brx', 'doi', 'en', 'gu', 'hi', 'kn', 'kok', 'ks', 'mai', 'ml', 'mni', 'mr', 'ne', 'or', 'pa', 'sa', 'sat', 'sd', 'ta', 'te', 'ur')

VOICES: dict[str, dict[str, list[str]]] = {'as': {'female': ['Prastuti'], 'male': ['Ankur']},
 'bn': {'female': ['Ishita'], 'male': ['Sourav']},
 'brx': {'female': ['Gwrbw'], 'male': ['Sansuma']},
 'doi': {'female': ['Preeti'], 'male': ['Sham']},
 'gu': {'female': ['Dhara'], 'male': ['Parth']},
 'hi': {'female': ['Kavya', 'Suhani'], 'male': ['Amit']},
 'kn': {'female': ['Deepika'], 'male': ['Adarsh']},
 'kok': {'female': ['Anjali'], 'male': ['Sandeep']},
 'ks': {'female': ['Zoon'], 'male': ['Ishfaq']},
 'mai': {'female': ['Vaidehi'], 'male': ['Madhukar']},
 'ml': {'female': ['Lakshmi'], 'male': ['Kiran']},
 'mni': {'female': ['Thoibi'], 'male': ['Chaoba']},
 'mr': {'female': ['Anagha'], 'male': ['Chinmay']},
 'ne': {'female': ['Srijana'], 'male': ['Sagar']},
 'or': {'female': ['Itishree'], 'male': ['Akash']},
 'pa': {'female': ['Kaur'], 'male': ['Manpreet']},
 'sa': {'female': ['Bharati'], 'male': ['Aryaman']},
 'sat': {'female': ['Phulmani'], 'male': ['Sibu']},
 'sd': {'female': ['Moomal'], 'male': ['Rano']},
 'ta': {'female': ['Anitha'], 'male': ['Arun']},
 'te': {'female': ['Sravani'], 'male': ['Vamsi']},
 'ur': {'female': ['Saba'], 'male': ['Zaid']}}

STYLES: tuple[str, ...] = ('AIR style news', 'Customer Care', 'TV style news', 'advertisements', 'anger', "children's stories", 'disgust', 'educational lecture', 'fear', 'happy', 'news', 'sad', 'single person narration audiobook', 'surprise')

ENGLISH_VOICE = "Amit"  # English has no voice row; any voice reads any language
MT_ASR_ONLY = ("bhb", "bho")


def unsupported_language(code: str, capability: str, allowed: tuple[str, ...]):
    from .errors import BodhanError

    name = LANGUAGE_NAMES.get(code, code)
    return BodhanError(
        "UNSUPPORTED_LANGUAGE",
        f"{name} ({code}) is not supported for {capability}. Nothing was generated and nothing was charged.",
        hint="Supported: " + ", ".join(allowed),
    )


def resolve_voice(language: str, voice: str | None, gender: str | None) -> str:
    """Pick a valid voice name. Voices are language-agnostic, but a name must exist in the library."""
    from .errors import BodhanError

    every = {v for g in VOICES.values() for names in g.values() for v in names}
    if voice:
        if voice not in every:
            local = sorted(v for names in VOICES.get(language, {}).values() for v in names)
            raise BodhanError(
                "UNSUPPORTED_VOICE",
                f"Voice '{voice}' is not in the Indic-Speak library.",
                hint="Valid for this language: " + ", ".join(local) if local else "See bodhan://voices.",
            )
        return voice
    if language not in VOICES:
        return ENGLISH_VOICE
    row = VOICES[language]
    for g in ([gender] if gender else []) + ["female", "male"]:
        if row.get(g):
            return row[g][0]
    return ENGLISH_VOICE
