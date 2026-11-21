import re
import unicodedata
from difflib import SequenceMatcher


class BackTranslationChecker:
    """
    Checks translation consistency by translating the translated text
    back into the original language using Bodhan Indic-Translate.

    Back-translation similarity is a warning signal, not proof
    of translation correctness.
    """

    def __init__(self, bodhan_client, threshold=0.55):
        self.client = bodhan_client
        self.threshold = threshold

    @staticmethod
    def normalize(text):
        """Normalize Unicode, whitespace and punctuation."""
        text = unicodedata.normalize("NFKC", text or "")
        text = text.lower()
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"[^\w\s]", "", text)
        return text.strip()

    @staticmethod
    def calculate_similarity(original, back_translated):
        """
        Character-level similarity.
        Useful for Indic scripts where word tokenization
        can be language-dependent.
        """
        original = BackTranslationChecker.normalize(original)
        back_translated = BackTranslationChecker.normalize(back_translated)

        if not original or not back_translated:
            return 0.0

        return round(
            SequenceMatcher(None, original, back_translated).ratio(),
            4
        )

    def evaluate(self, source, translation, source_language):
        """
        Translate the provided translation back into the source language
        and compare it with the original source.
        """

        if not source or not translation:
            return {
                "status": "failed",
                "back_translation": "",
                "similarity": 0.0,
                "error": "Source or translation is empty."
            }

        result = self.client.translate(
            text=translation,
            target_language=source_language
        )

        back_translation = result["translation"]

        similarity = self.calculate_similarity(
            source,
            back_translation
        )

        status = (
            "passed"
            if similarity >= self.threshold
            else "warning"
        )

        return {
            "status": status,
            "back_translation": back_translation,
            "similarity": similarity,
            "threshold": self.threshold,
            "error": None
        }