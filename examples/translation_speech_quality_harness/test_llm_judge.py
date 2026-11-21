import json

from bodhan_eval.llm_judge import LLMJudge


class MockLLMJudge(LLMJudge):
    """Mock LLM response to avoid API calls during testing."""

    def _call_llm(self, prompt):

        return json.dumps({
            "score": 5,
            "meaning_preserved": True,
            "fluency": 5,
            "completeness": 5,
            "hallucination": False,
            "overall_severity": "none",
            "errors": [],
            "reasoning": (
                "The translation preserves the source meaning "
                "and is fluent."
            )
        })


def main():

    judge = MockLLMJudge()

    print("=" * 55)
    print("INDICEVAL - LLM JUDGE TEST")
    print("=" * 55)

    source = "नमस्ते, आप कैसे हैं?"
    translation = "Hello, how are you?"

    result = judge.evaluate(
        source=source,
        translation=translation,
        source_language="hi",
        target_language="en"
    )

    print("\nSource:", source)
    print("Translation:", translation)

    print("\nEvaluation:")
    print(json.dumps(result, indent=4, ensure_ascii=False))

    assert result["score"] == 5
    assert result["meaning_preserved"] is True
    assert result["overall_severity"] == "none"
    assert result["errors"] == []

    # Test invalid JSON.
    print("\n[TEST 2] Invalid JSON handling")

    try:
        judge.parse_response("This is not JSON")
        raise AssertionError("Invalid JSON was accepted.")

    except Exception as exc:
        print("Correctly rejected:", exc)

    # Test invalid error span.
    print("\n[TEST 3] Invalid error span handling")

    class InvalidSpanJudge(MockLLMJudge):
        def _call_llm(self, prompt):
            return json.dumps({
                "score": 2,
                "meaning_preserved": False,
                "fluency": 2,
                "completeness": 2,
                "hallucination": False,
                "overall_severity": "major",
                "errors": [
                    {
                        "category": "meaning_error",
                        "severity": "major",
                        "span": "This text does not exist",
                        "explanation": "Meaning was changed."
                    }
                ],
                "reasoning": "Incorrect translation."
            })

    invalid_judge = InvalidSpanJudge()

    try:
        invalid_judge.evaluate(
            source="नमस्ते",
            translation="Hello",
            source_language="hi",
            target_language="en"
        )

        raise AssertionError("Invalid span was accepted.")

    except Exception as exc:
        print("Correctly rejected:", exc)

    print("\n" + "=" * 55)
    print("ALL LLM JUDGE TESTS PASSED!")
    print("=" * 55)


if __name__ == "__main__":
    main()