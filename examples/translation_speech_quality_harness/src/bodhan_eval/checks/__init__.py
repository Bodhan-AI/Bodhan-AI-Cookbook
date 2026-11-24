"""Check registry. List position is run order: cheap, local checks first; model round trips next; the LLM judge last,
so it can see every earlier finding. Speech checks run after translation checks."""

from .base import Check, DatasetStats, SkipCheck
from .deterministic import FormatCheck, LengthCheck, ReferenceCheck, ScriptCheck
from .judge import JudgeCheck
from .roundtrip import BackTranslationCheck, EntityCheck, NumberCheck, consistency_signals
from .speech import AsrCheck, AudioStatsCheck

TRANSLATION_CHECKS: list[type[Check]] = [
    ScriptCheck,  # tier 0
    FormatCheck,  # tier 0
    LengthCheck,  # tier 0
    ReferenceCheck,  # tier 0 (metrics only)
    BackTranslationCheck,  # tier 1: Indic-Translate target -> en
    NumberCheck,  # tier 0 logic, consults the back-translation if present
    EntityCheck,  # tier 1: Indic-Transliterate + back-translation
    JudgeCheck,  # tier 2: LLM
]

SPEECH_CHECKS: list[type[Check]] = [
    AudioStatsCheck,  # tier 0: waveform statistics
    AsrCheck,  # tier 1: Indic-Transcribe + alignment
]

DATASET_CHECKS = [consistency_signals]

__all__ = ["Check", "DatasetStats", "SkipCheck", "TRANSLATION_CHECKS", "SPEECH_CHECKS", "DATASET_CHECKS"]
