import re
import logging
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)


class TranslationQualityChecker:
    """
    Deterministic translation quality checks.

    Checks:
        1. Empty translation
        2. Number preservation
        3. Named entity preservation (heuristic)
        4. Markdown integrity
        5. LaTeX integrity
        6. Source and translation length anomalies
        7. Repeated word detection
        8. Terminal punctuation integrity
        9. Omission detection (English same-language comparison)
        10. Addition detection (English same-language comparison)

    Important:
        Word-level omission and addition checks are only enabled
        for English because direct lexical comparison across
        different languages is unreliable.
    """

    def __init__(self):

        self.number_pattern = re.compile(
            r"""
            (?:₹|\$|€|£)?\s*
            [-+]?
            \d[\d,]*(?:\.\d+)?
            \s*(?:%|percent)?
            """,
            re.VERBOSE
        )

        self.markdown_tokens = [
            "**", "__", "```", "`", "~~",
            "# ", "- ", "> "
        ]

        self.latex_delimiters = [
            ("$$", "$$"),
            (r"\(", r"\)"),
            (r"\[", r"\]")
        ]

        # Unicode-aware word extraction.
        self.word_pattern = re.compile(
            r"[^\W_]+(?:['’][^\W_]+)*",
            re.UNICODE
        )

        self.terminal_punctuation = {
            ".", "!", "?", "।", "॥"
        }

        # Common English function words.
        # These are excluded from lexical omission/addition checks.
        self.english_stopwords = {
            "a", "an", "the", "and", "or", "but",
            "if", "while", "with", "without",
            "of", "at", "by", "for", "from",
            "to", "in", "on", "into", "onto",
            "is", "am", "are", "was", "were",
            "be", "been", "being",
            "has", "have", "had",
            "do", "does", "did",
            "will", "would", "shall", "should",
            "can", "could", "may", "might", "must",
            "this", "that", "these", "those",
            "it", "its", "they", "them",
            "he", "she", "we", "you", "i",
            "me", "my", "our", "your", "their",
            "his", "her",
            "as", "than", "then",
            "not", "no", "so", "very",
            "also", "just", "only",
            "there", "here",
            "when", "where", "what", "which",
            "who", "whom", "how",
            "about", "after", "before",
            "up", "down", "out", "over",
            "again", "once"
        }

    # =====================================================
    # TEXT HELPERS
    # =====================================================

    def extract_words(self, text: str):
        """Extract normalized words."""

        return [
            word.casefold()
            for word in self.word_pattern.findall(text or "")
        ]

    def normalize_content_word(self, word: str) -> str:
        """
        Normalize a small, explicit set of common English synonyms
        and inflectional variants before lexical comparison.

        This is a lightweight heuristic, not semantic understanding.
        """

        synonym_groups = {
            "company": {"company", "firm"},
            "receive": {"received", "receive", "receives", "got", "get"},
            "customer": {"customer", "customers", "client", "clients"},
            "delivery": {"delivery", "deliveries", "shipment", "shipments"},
            "arrive": {"arrive", "arrives", "arrived", "reach", "reaches", "reached"},
            "start": {"start", "starts", "started", "begin", "begins", "began"},
            "launch": {"launch", "launched", "launches", "introduce", "introduced", "introduces"},
            "product": {"product", "products", "item", "items"},
            "confirm": {"confirmation", "confirming", "confirmed", "confirms", "confirm"},
            "email": {"email", "emails", "e-mail", "e-mails"},
            "cost": {"cost", "costs", "costing", "price", "prices"},
            "complete": {"complete", "completed", "completes", "finish", "finished"},
            "success": {"successfully", "successful", "success"},
        }

        for canonical, variants in synonym_groups.items():
            if word in variants:
                return canonical

        # Normalize common English number words. Numeric tokens are
        # subsequently excluded from lexical checks; number preservation
        # is handled separately by check_number_preservation().
        number_words = {
            "zero": "0",
            "one": "1",
            "two": "2",
            "three": "3",
            "four": "4",
            "five": "5",
            "six": "6",
            "seven": "7",
            "eight": "8",
            "nine": "9",
            "ten": "10",
        }

        return number_words.get(word, word)

    def extract_content_words(self, text: str):
        """
        Extract normalized English content words.

        Stopwords and purely numeric tokens are excluded. A small
        synonym map reduces obvious paraphrase-related false positives.
        """

        words = self.extract_words(text)
        normalized_words = [
            self.normalize_content_word(word)
            for word in words
        ]

        return [
            word
            for word in normalized_words
            if (
                word not in self.english_stopwords
                and not word.isdigit()
            )
        ]

    # =====================================================
    # 1. EMPTY TRANSLATION
    # =====================================================

    def check_empty_translation(self, translation: str):

        if not translation or not translation.strip():
            return {
                "check": "empty_translation",
                "status": "failed",
                "severity": "critical",
                "message": "Translation is empty.",
                "evidence": translation
            }

        return {
            "check": "empty_translation",
            "status": "passed",
            "severity": "none",
            "message": "Translation is not empty.",
            "evidence": None
        }

    # =====================================================
    # 2. NUMBER PRESERVATION
    # =====================================================

    def extract_numbers(self, text: str):

        matches = self.number_pattern.findall(text)
        numbers = []

        for match in matches:
            normalized = re.sub(r"\s+", "", match)
            normalized = normalized.replace(",", "")

            if normalized:
                numbers.append(normalized)

        return numbers

    def check_number_preservation(
        self,
        source: str,
        translation: str
    ):

        source_numbers = self.extract_numbers(source)
        translated_numbers = self.extract_numbers(translation)

        missing = []
        remaining = translated_numbers.copy()

        for number in source_numbers:

            if number in remaining:
                remaining.remove(number)
            else:
                missing.append(number)

        if missing:
            return {
                "check": "number_preservation",
                "status": "failed",
                "severity": "high",
                "message": (
                    "Some source numbers are missing or changed "
                    "in translation."
                ),
                "evidence": {
                    "source_numbers": source_numbers,
                    "translated_numbers": translated_numbers,
                    "missing_numbers": missing
                }
            }

        return {
            "check": "number_preservation",
            "status": "passed",
            "severity": "none",
            "message": "All detected source numbers are preserved.",
            "evidence": {
                "source_numbers": source_numbers,
                "translated_numbers": translated_numbers
            }
        }

    # =====================================================
    # 3. NAMED ENTITY PRESERVATION
    # =====================================================

    def extract_entities(self, text: str):
        """
        Heuristically extract Latin-script proper names.

        This is not a full multilingual NER system. A small
        case-insensitive ignore list prevents common sentence
        starters and function words (for example, "Its" and
        "The") from being mistaken for named entities.
        """

        pattern = re.compile(
            r"\b[A-Z][a-zA-Z]*(?:\s+[A-Z][a-zA-Z]*)*\b"
        )

        ignored = {
            # Articles, pronouns and demonstratives
            "the", "a", "an", "this", "that", "these", "those",
            "i", "we", "he", "she", "they", "it", "its",
            "you", "my", "our", "your", "their", "his", "her",
            # Common conjunctions, prepositions and sentence starters
            "and", "or", "but", "so", "if", "as", "in", "on",
            "at", "by", "for", "from", "with", "without", "to",
            "of", "after", "before", "during", "while", "when",
            "where", "however", "therefore", "meanwhile", "also",
            "then", "finally", "first", "next",
            # Weekdays and months
            "monday", "tuesday", "wednesday", "thursday", "friday",
            "saturday", "sunday", "january", "february", "march",
            "april", "may", "june", "july", "august", "september",
            "october", "november", "december"
        }

        entities = []

        for match in pattern.findall(text or ""):
            if match.casefold() not in ignored:
                entities.append(match)

        return entities

    def check_entity_preservation(
        self,
        source: str,
        translation: str
    ):

        source_entities = self.extract_entities(source)
        translated_entities = self.extract_entities(translation)

        missing = [
            entity
            for entity in source_entities
            if entity not in translated_entities
        ]

        if missing:
            return {
                "check": "entity_preservation",
                "status": "warning",
                "severity": "medium",
                "message": (
                    "Some detected Latin-script names "
                    "are not present in the translation."
                ),
                "evidence": {
                    "source_entities": source_entities,
                    "translated_entities": translated_entities,
                    "missing_entities": missing
                }
            }

        return {
            "check": "entity_preservation",
            "status": "passed",
            "severity": "none",
            "message": "No missing Latin-script entities detected.",
            "evidence": {
                "source_entities": source_entities,
                "translated_entities": translated_entities
            }
        }

    # =====================================================
    # 4. MARKDOWN INTEGRITY
    # =====================================================

    def check_markdown_integrity(
        self,
        source: str,
        translation: str
    ):

        errors = []

        for token in self.markdown_tokens:

            source_count = source.count(token)
            translated_count = translation.count(token)

            if source_count != translated_count:
                errors.append({
                    "token": token,
                    "source_count": source_count,
                    "translation_count": translated_count
                })

        if errors:
            return {
                "check": "markdown_integrity",
                "status": "failed",
                "severity": "medium",
                "message": "Markdown formatting tokens changed.",
                "evidence": errors
            }

        return {
            "check": "markdown_integrity",
            "status": "passed",
            "severity": "none",
            "message": "Markdown formatting tokens are preserved.",
            "evidence": None
        }

    # =====================================================
    # 5. LATEX INTEGRITY
    # =====================================================

    def check_latex_integrity(
        self,
        source: str,
        translation: str
    ):

        errors = []

        for opening, closing in self.latex_delimiters:

            source_open = source.count(opening)
            source_close = source.count(closing)

            translated_open = translation.count(opening)
            translated_close = translation.count(closing)

            if (
                source_open != translated_open
                or source_close != translated_close
            ):
                errors.append({
                    "opening_delimiter": opening,
                    "closing_delimiter": closing,
                    "source_opening_count": source_open,
                    "translation_opening_count": translated_open,
                    "source_closing_count": source_close,
                    "translation_closing_count": translated_close
                })

        if errors:
            return {
                "check": "latex_integrity",
                "status": "failed",
                "severity": "high",
                "message": "LaTeX delimiters may have been corrupted.",
                "evidence": errors
            }

        return {
            "check": "latex_integrity",
            "status": "passed",
            "severity": "none",
            "message": "LaTeX delimiters are preserved.",
            "evidence": None
        }

    # =====================================================
    # 6. LENGTH ANOMALY
    # =====================================================

    def check_length_anomaly(
        self,
        source: str,
        translation: str
    ):

        source_length = len(source.strip())
        translation_length = len(translation.strip())

        ratio = (
            translation_length / source_length
            if source_length > 0 else None
        )

        if ratio is not None and (
            ratio < 0.1 or ratio > 5.0
        ):
            return {
                "check": "length_anomaly",
                "status": "warning",
                "severity": "low",
                "message": "Translation length is unusually different.",
                "evidence": {
                    "source_length": source_length,
                    "translation_length": translation_length,
                    "length_ratio": round(ratio, 3)
                }
            }

        return {
            "check": "length_anomaly",
            "status": "passed",
            "severity": "none",
            "message": "No extreme length difference detected.",
            "evidence": {
                "source_length": source_length,
                "translation_length": translation_length,
                "length_ratio": (
                    round(ratio, 3)
                    if ratio is not None else None
                )
            }
        }

    # =====================================================
    # 7. REPETITION DETECTION
    # =====================================================

    def check_repetition(self, translation: str):

        words = self.word_pattern.findall(translation)
        normalized_words = [
            word.casefold()
            for word in words
        ]

        repeated = []

        for index in range(1, len(normalized_words)):

            if normalized_words[index] == normalized_words[index - 1]:
                repeated.append({
                    "word": words[index],
                    "position": index,
                    "repeated_sequence": (
                        words[index - 1] + " " + words[index]
                    )
                })

        if repeated:
            return {
                "check": "repetition",
                "status": "failed",
                "severity": "medium",
                "message": "Consecutive repeated words detected.",
                "evidence": repeated
            }

        return {
            "check": "repetition",
            "status": "passed",
            "severity": "none",
            "message": "No consecutive repeated words detected.",
            "evidence": None
        }

    # =====================================================
    # 8. TERMINAL PUNCTUATION INTEGRITY
    # =====================================================

    def check_punctuation_integrity(
        self,
        source: str,
        translation: str
    ):

        source_text = source.rstrip()
        translated_text = translation.rstrip()

        source_has_terminal = (
            bool(source_text)
            and source_text[-1] in self.terminal_punctuation
        )

        translation_has_terminal = (
            bool(translated_text)
            and translated_text[-1] in self.terminal_punctuation
        )

        if source_has_terminal and not translation_has_terminal:
            return {
                "check": "punctuation_integrity",
                "status": "warning",
                "severity": "low",
                "message": (
                    "Source has terminal punctuation, "
                    "but translation does not."
                ),
                "evidence": {
                    "source_ending": source_text[-1],
                    "translation_ending": (
                        translated_text[-1]
                        if translated_text else None
                    )
                }
            }

        return {
            "check": "punctuation_integrity",
            "status": "passed",
            "severity": "none",
            "message": "No missing terminal punctuation detected.",
            "evidence": {
                "source_has_terminal_punctuation": source_has_terminal,
                "translation_has_terminal_punctuation": (
                    translation_has_terminal
                )
            }
        }

    # =====================================================
    # 9. OMISSION DETECTION
    # =====================================================

    def check_omission(
        self,
        source: str,
        translation: str,
        language: str = "en"
    ):
        """
        Detect missing English content words.

        This is a lexical heuristic, not semantic alignment.
        It is disabled for non-English source languages.
        """

        if language.lower() not in ("en", "eng", "english"):

            return {
                "check": "omission_detection",
                "status": "skipped",
                "severity": "none",
                "message": (
                    "Lexical omission detection is limited "
                    "to English same-language comparisons."
                ),
                "evidence": None
            }

        source_words = self.extract_content_words(source)
        translated_words = self.extract_content_words(translation)

        source_counts = Counter(source_words)
        translation_counts = Counter(translated_words)

        missing = []

        for word, count in source_counts.items():

            missing_count = count - translation_counts.get(word, 0)

            if missing_count > 0:
                missing.extend([word] * missing_count)

        if missing:
            return {
                "check": "omission_detection",
                "status": "warning",
                "severity": "medium",
                "message": (
                    "Some English content words from the source "
                    "are missing in the translation."
                ),
                "evidence": {
                    "missing_words": missing,
                    "source_content_words": source_words,
                    "translation_content_words": translated_words
                }
            }

        return {
            "check": "omission_detection",
            "status": "passed",
            "severity": "none",
            "message": "No missing English content words detected.",
            "evidence": {
                "source_content_words": source_words,
                "translation_content_words": translated_words
            }
        }

    # =====================================================
    # 10. ADDITION DETECTION
    # =====================================================

    def check_addition(
        self,
        source: str,
        translation: str,
        language: str = "en"
    ):
        """
        Detect additional English content words.

        This is a lexical heuristic, not semantic alignment.
        It is disabled for non-English source languages.
        """

        if language.lower() not in ("en", "eng", "english"):

            return {
                "check": "addition_detection",
                "status": "skipped",
                "severity": "none",
                "message": (
                    "Lexical addition detection is limited "
                    "to English same-language comparisons."
                ),
                "evidence": None
            }

        source_words = self.extract_content_words(source)
        translated_words = self.extract_content_words(translation)

        source_counts = Counter(source_words)
        translation_counts = Counter(translated_words)

        additional = []

        for word, count in translation_counts.items():

            extra_count = count - source_counts.get(word, 0)

            if extra_count > 0:
                additional.extend([word] * extra_count)

        if additional:
            return {
                "check": "addition_detection",
                "status": "warning",
                "severity": "medium",
                "message": (
                    "Additional English content words were "
                    "detected in the translation."
                ),
                "evidence": {
                    "additional_words": additional,
                    "source_content_words": source_words,
                    "translation_content_words": translated_words
                }
            }

        return {
            "check": "addition_detection",
            "status": "passed",
            "severity": "none",
            "message": "No additional English content words detected.",
            "evidence": {
                "source_content_words": source_words,
                "translation_content_words": translated_words
            }
        }

    # =====================================================
    # 11. COMPLETE QUALITY EVALUATION
    # =====================================================

    def evaluate(
        self,
        item_id: str,
        source: str,
        translation: str,
        language: str = "unknown"
    ) -> dict[str, Any]:

        checks = []

        empty_result = self.check_empty_translation(translation)
        checks.append(empty_result)

        if empty_result["status"] == "failed":

            return {
                "item_id": item_id,
                "language": language,
                "source": source,
                "translation": translation,
                "checks": checks,
                "overall_status": "failed",
                "errors": [empty_result]
            }

        checks.append(
            self.check_number_preservation(source, translation)
        )

        checks.append(
            self.check_entity_preservation(source, translation)
        )

        checks.append(
            self.check_markdown_integrity(source, translation)
        )

        checks.append(
            self.check_latex_integrity(source, translation)
        )

        checks.append(
            self.check_length_anomaly(source, translation)
        )

        checks.append(
            self.check_repetition(translation)
        )

        checks.append(
            self.check_punctuation_integrity(source, translation)
        )

        checks.append(
            self.check_omission(
                source,
                translation,
                language
            )
        )

        checks.append(
            self.check_addition(
                source,
                translation,
                language
            )
        )

        failures = [
            result for result in checks
            if result["status"] == "failed"
        ]

        warnings = [
            result for result in checks
            if result["status"] == "warning"
        ]

        if failures:
            overall_status = "failed"

        elif warnings:
            overall_status = "warning"

        else:
            overall_status = "passed"

        errors = [
            result for result in checks
            if result["status"] in ("failed", "warning")
        ]

        return {
            "item_id": item_id,
            "language": language,
            "source": source,
            "translation": translation,
            "checks": checks,
            "overall_status": overall_status,
            "errors": errors
        }