import random
import re
from copy import deepcopy


class ErrorInjector:
    """
    Programmatically injects controlled errors into translations.

    Every injected example receives a ground-truth label
    for calibration and evaluation.
    """

    def __init__(self, seed=42):
        self.random = random.Random(seed)

    @staticmethod
    def change_number(text):
        """Change a number to a different value."""

        pattern = re.compile(r"\d[\d,]*(?:\.\d+)?")

        match = pattern.search(text)

        if not match:
            return None

        original = match.group()

        digits = re.sub(r"\D", "", original)

        if not digits:
            return None

        changed = str(int(digits) + 100)

        corrupted = (
            text[:match.start()]
            + changed
            + text[match.end():]
        )

        return {
            "text": corrupted,
            "category": "number_error",
            "severity": "major",
            "original_span": original,
            "corrupted_span": changed
        }

    @staticmethod
    def omit_phrase(text):
        """Remove a meaningful phrase from the translation."""

        words = text.split()

        if len(words) < 4:
            return None

        # Remove approximately one-third of the words.
        start = len(words) // 3
        end = min(start + 2, len(words))

        removed = " ".join(words[start:end])

        corrupted_words = words[:start] + words[end:]

        return {
            "text": " ".join(corrupted_words),
            "category": "omission",
            "severity": "major",
            "original_span": removed,
            "corrupted_span": ""
        }

    @staticmethod
    def repeat_word(text):
        """Duplicate a word to simulate speech or translation repetition."""

        words = text.split()

        if not words:
            return None

        index = len(words) // 2
        word = words[index]

        corrupted_words = (
            words[:index]
            + [word, word]
            + words[index + 1:]
        )

        return {
            "text": " ".join(corrupted_words),
            "category": "repetition",
            "severity": "minor",
            "original_span": word,
            "corrupted_span": f"{word} {word}"
        }

    @staticmethod
    def remove_punctuation(text):
        """Remove punctuation from the translation."""

        corrupted = re.sub(r"[^\w\s]", "", text)

        if corrupted == text:
            return None

        return {
            "text": corrupted,
            "category": "formatting_error",
            "severity": "minor",
            "original_span": text,
            "corrupted_span": corrupted
        }

    @staticmethod
    def add_information(text):
        """
        Add a clearly marked unsupported statement.

        This English-only injector is intended for English
        translations. It does not generate Indic-language text.
        """

        addition = " Additional unsupported information."

        return {
            "text": text.rstrip() + addition,
            "category": "addition",
            "severity": "major",
            "original_span": "",
            "corrupted_span": addition.strip()
        }

    def inject(self, translation, category):
        """Inject one selected error category."""

        injectors = {
            "number_error": self.change_number,
            "omission": self.omit_phrase,
            "repetition": self.repeat_word,
            "formatting_error": self.remove_punctuation,
            "addition": self.add_information
        }

        if category not in injectors:
            raise ValueError(
                f"Unsupported error category: {category}"
            )

        result = injectors[category](translation)

        if result is None:
            return None

        return {
            "original_translation": translation,
            "corrupted_translation": result["text"],
            "ground_truth": {
                "category": result["category"],
                "severity": result["severity"],
                "original_span": result["original_span"],
                "corrupted_span": result["corrupted_span"]
            }
        }

    def inject_all(self, translation):
        """Generate all applicable error variants."""

        results = []

        categories = [
            "number_error",
            "omission",
            "repetition",
            "formatting_error",
            "addition"
        ]

        for category in categories:
            result = self.inject(translation, category)

            if result is not None:
                results.append(result)

        return results

    def generate_dataset(self, translations):
        """
        Generate a labelled dataset from a list of translations.
        """

        dataset = []

        for index, translation in enumerate(translations):
            variants = self.inject_all(translation)

            for variant in variants:
                record = deepcopy(variant)

                record["id"] = (
                    f"injected_{index}_"
                    f"{record['ground_truth']['category']}"
                )

                dataset.append(record)

        return dataset