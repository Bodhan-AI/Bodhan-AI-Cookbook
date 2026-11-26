import logging
import re

logger = logging.getLogger(__name__)

class EnglishCondenser:
    """Agentic module for English-side text condensation.

    STRICT RULE: Only the English source text is ever condensed or modified.
    Target Indic translations are NEVER edited directly by this module.
    """

    # Common filler words and redundant English phrases that can be safely simplified
    FILLERS = [
        (r"\bkind of\b", ""),
        (r"\bsort of\b", ""),
        (r"\bas a matter of fact\b", "actually"),
        (r"\bat the end of the day\b", "ultimately"),
        (r"\bin order to\b", "to"),
        (r"\bdue to the fact that\b", "because"),
        (r"\bfor the purpose of\b", "to"),
        (r"\bin the event that\b", "if"),
        (r"\bplease be aware that\b", "note that"),
        (r"\bit is important to note that\b", ""),
        (r"\bas you can see\b", ""),
        (r"\bbasically\b", ""),
        (r"\bactually\b", ""),
        (r"\bliterally\b", ""),
        (r"\bvery\b", ""),
        (r"\breally\b", ""),
    ]

    def condense(self, english_text: str, target_ratio: float = 0.85) -> str:
        """Condenses English text to reduce its spoken syllable count and duration.

        Args:
            english_text: Original source English segment text.
            target_ratio: Target length ratio (e.g. 0.80 for 20% reduction).
        """
        if not english_text.strip():
            return english_text

        text = english_text.strip()

        # Step 1: Remove common verbose filler phrases
        for pattern, replacement in self.FILLERS:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

        # Clean multiple spaces and dangling punctuation
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"\s+([,.:;?!])", r"\1", text)

        words = text.split()
        target_word_count = max(3, int(len(english_text.split()) * target_ratio))

        # Step 2: If further reduction is required, condense auxiliary phrasing
        if len(words) > target_word_count:
            # Simplify passive and progressive constructs
            text = re.sub(r"\bis able to\b", "can", text, flags=re.IGNORECASE)
            text = re.sub(r"\bare able to\b", "can", text, flags=re.IGNORECASE)
            text = re.sub(r"\bhas the ability to\b", "can", text, flags=re.IGNORECASE)
            text = re.sub(r"\bwould like to\b", "wants to", text, flags=re.IGNORECASE)
            text = re.sub(r"\bmake an adjustment\b", "adjust", text, flags=re.IGNORECASE)
            text = re.sub(r"\btake into consideration\b", "consider", text, flags=re.IGNORECASE)
            text = re.sub(r"\s+", " ", text).strip()

        logger.info("Condensed English: '%s' -> '%s' (chars: %d -> %d)",
                    english_text, text, len(english_text), len(text))
        return text
