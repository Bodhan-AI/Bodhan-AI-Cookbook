"""Bodhan: Indic translate / transcribe / speak for JARVIS (Telugu, Hindi, English)."""
from .client import (LANGUAGES, BodhanError, answer_to_language, query_to_english,
                     speak, to_english, to_language, transcribe)

__all__ = ["LANGUAGES", "BodhanError", "answer_to_language", "query_to_english",
           "speak", "to_english", "to_language", "transcribe"]
