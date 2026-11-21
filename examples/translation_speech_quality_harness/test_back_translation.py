from bodhan_eval.back_translation import BackTranslationChecker


class MockBodhanClient:
    """Mock client so tests do not make paid API calls."""

    def translate(self, text, target_language):
        return {
            "translation": "नमस्ते आप कैसे हैं?"
        }


def main():
    checker = BackTranslationChecker(
        bodhan_client=MockBodhanClient(),
        threshold=0.55
    )

    result = checker.evaluate(
        source="नमस्ते, आप कैसे हैं?",
        translation="Hello, how are you?",
        source_language="hi"
    )

    print("INDICEVAL - BACK TRANSLATION TEST")
    print("=" * 45)
    print("Original:", "नमस्ते, आप कैसे हैं?")
    print("Translation:", "Hello, how are you?")
    print("Back translation:", result["back_translation"])
    print("Similarity:", result["similarity"])
    print("Status:", result["status"])

    assert result["status"] == "passed"
    assert result["similarity"] >= 0.55

    print("\nTest passed!")


if __name__ == "__main__":
    main()