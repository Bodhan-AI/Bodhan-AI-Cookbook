from bodhan_eval.evaluator import IndicEvalEvaluator


class FakeLLMJudge:
    """Simulates a Groq API rate-limit failure."""

    def evaluate(self, **kwargs):
        raise RuntimeError(
            "429 Too Many Requests - simulated test"
        )


def main():

    evaluator = IndicEvalEvaluator(
        run_back_translation=False,
        run_llm_judge=True,
        run_speech=False,
        llm_judge=FakeLLMJudge()
    )

    record = {
        "id": "infrastructure_test_001",
        "source": "The delivery will arrive tomorrow.",
        "translation": "The delivery will arrive tomorrow.",
        "lang": "en",
        "source_lang": "en",
        "target_lang": "en"
    }

    result = evaluator.evaluate_item(record)

    print("\n========== ERROR HANDLING TEST ==========")

    print("Translation status:", result["translation_status"])
    print("Evaluation status:", result["evaluation_status"])

    print("\nTranslation errors:")
    print(result["errors"])

    print("\nEvaluation errors:")
    print(result["evaluation_errors"])

    assert result["translation_status"] == "passed"

    assert result["evaluation_status"] == "partial"

    assert len(result["errors"]) == 0

    assert len(result["evaluation_errors"]) == 1

    print("\nSUCCESS: API failure was not classified as a translation error.")


if __name__ == "__main__":
    main()