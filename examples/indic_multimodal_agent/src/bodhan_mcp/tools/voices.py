"""Voice registry for Indic-Speak speech synthesis.
Compiled strictly from official Bodhan AI Indic-Speak documentation and model cards
(https://huggingface.co/bodhan-ai/indic-speak).

Official Indic-Speak model documentation notes that voices are cross-language:
Speaker identity embeddings are decoupled from language conditioning, enabling any
trained voice persona to synthesize speech across all 22 scheduled Indian languages
and English. Each voice persona has an optimized primary/native language where
its intonation and accent are native.
"""

from typing import Dict, List, Optional
from pydantic import BaseModel


class VoiceProfile(BaseModel):
    name: str
    language: str              # Native/primary language code (e.g. 'hi', 'bn', 'te')
    primary_language: str      # Explicit alias for primary language
    gender: str                # 'male', 'female', or 'unknown'
    cross_language: bool = True # Indic-Speak voices can speak across all supported Indic languages
    description: str


# Officially verified voices from Bodhan AI docs and model cards
OFFICIAL_VOICES: List[VoiceProfile] = [
    VoiceProfile(name="Amit", language="hi", primary_language="hi", gender="male", cross_language=True, description="Standard male voice for Hindi and multilingual cross-language delivery"),
    VoiceProfile(name="Priya", language="hi", primary_language="hi", gender="female", cross_language=True, description="Standard female voice for Hindi and multilingual cross-language delivery"),
    VoiceProfile(name="Ravi", language="te", primary_language="te", gender="male", cross_language=True, description="Male voice for Telugu and southern regional cross-language delivery"),
    VoiceProfile(name="Kavitha", language="ta", primary_language="ta", gender="female", cross_language=True, description="Female voice for Tamil and cross-language delivery"),
    VoiceProfile(name="Ananya", language="bn", primary_language="bn", gender="female", cross_language=True, description="Female voice for Bengali and cross-language delivery"),
    VoiceProfile(name="Suresh", language="kn", primary_language="kn", gender="male", cross_language=True, description="Male voice for Kannada and cross-language delivery"),
    VoiceProfile(name="Neha", language="mr", primary_language="mr", gender="female", cross_language=True, description="Female voice for Marathi and cross-language delivery"),
]


def get_voice_profile(name: str) -> Optional[VoiceProfile]:
    """Retrieve voice profile by name."""
    name_clean = name.strip().lower()
    for v in OFFICIAL_VOICES:
        if v.name.lower() == name_clean:
            return v
    return None


def list_voices(language: Optional[str] = None, gender: Optional[str] = None) -> List[VoiceProfile]:
    """List supported voices filtered optionally by language and gender.
    
    If language is provided, primary language matches are returned first.
    If no primary match exists for that language + gender combination,
    available cross-language voices matching gender are returned.
    """
    results = OFFICIAL_VOICES
    if language:
        lang_clean = language.lower().strip()
        primary = [v for v in results if v.primary_language == lang_clean]
        if gender:
            gender_clean = gender.lower().strip()
            primary_gender = [v for v in primary if v.gender == gender_clean]
            if primary_gender:
                return primary_gender
            # No native voice matching this gender; return cross-language voices matching gender
            cross = [v for v in results if v.gender == gender_clean and v.cross_language]
            return cross
        if primary:
            return primary

    if gender:
        gender_clean = gender.lower().strip()
        results = [v for v in results if v.gender == gender_clean]
    return results


def get_default_voice(language: str, gender: Optional[str] = None) -> str:
    """Get best matching default voice name for language and gender.
    
    Returns native voice if available, otherwise falls back to cross-language voice.
    """
    matches = list_voices(language=language, gender=gender)
    if matches:
        return matches[0].name
    return "Amit"

