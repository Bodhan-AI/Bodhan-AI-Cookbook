import json

from bodhan_eval.evaluator import IndicEvalEvaluator


def main():

    print("=" * 60)
    print("INDICEVAL - MAIN PIPELINE TEST")
    print("=" * 60)

    # Disable API-based components for this test.
    evaluator = IndicEvalEvaluator(
        run_back_translation=False,
        run_llm_judge=False,
        run_speech=False
    )

    dataset_path = "sample_data/pipeline_test.jsonl"

    results = evaluator.evaluate_dataset(dataset_path)

    print("\nEvaluation results:\n")

    for result in results:
        print(
            json.dumps(
                result,
                indent=4,
                ensure_ascii=False
            )
        )

    # Verify the number of evaluated records.
    assert len(results) == 3

    # First translation should pass.
    assert results[0]["status"] == "passed"

    # Second translation should fail due to changed number.
    assert results[1]["status"] == "failed"

    # Third translation should pass.
    assert results[2]["status"] == "passed"

    # Save results.
    output_path = "outputs/pipeline_test_results.jsonl"

    with open(output_path, "w", encoding="utf-8") as file:

        for result in results:
            file.write(
                json.dumps(result, ensure_ascii=False) + "\n"
            )

    print("\nSaved:", output_path)

    print("\n" + "=" * 60)
    print("ALL PIPELINE TESTS PASSED!")
    print("=" * 60)


if __name__ == "__main__":
    main()