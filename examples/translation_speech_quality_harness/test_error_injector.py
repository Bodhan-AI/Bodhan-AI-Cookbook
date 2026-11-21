import json

from bodhan_eval.error_injector import ErrorInjector


def main():

    injector = ErrorInjector(seed=42)

    translation = (
        "The company received a payment of Rs 500 "
        "from the customer in New Delhi."
    )

    print("=" * 60)
    print("INDICEVAL - ERROR INJECTION TEST")
    print("=" * 60)

    print("\nOriginal translation:")
    print(translation)

    # Test 1: Number error
    print("\n[TEST 1] Number error")

    result = injector.inject(
        translation,
        "number_error"
    )

    print(json.dumps(result, indent=4))

    assert result is not None
    assert result["ground_truth"]["category"] == "number_error"
    assert "600" in result["corrupted_translation"]

    # Test 2: Omission
    print("\n[TEST 2] Omission")

    result = injector.inject(
        translation,
        "omission"
    )

    print(json.dumps(result, indent=4))

    assert result is not None
    assert result["ground_truth"]["category"] == "omission"

    # Test 3: Repetition
    print("\n[TEST 3] Repetition")

    result = injector.inject(
        translation,
        "repetition"
    )

    print(json.dumps(result, indent=4))

    assert result is not None
    assert result["ground_truth"]["category"] == "repetition"

    # Test 4: Formatting error
    print("\n[TEST 4] Formatting error")

    result = injector.inject(
        translation,
        "formatting_error"
    )

    print(json.dumps(result, indent=4))

    assert result is not None
    assert result["ground_truth"]["category"] == "formatting_error"

    # Test 5: Addition
    print("\n[TEST 5] Addition")

    result = injector.inject(
        translation,
        "addition"
    )

    print(json.dumps(result, indent=4))

    assert result is not None
    assert result["ground_truth"]["category"] == "addition"

    # Test 6: Full dataset generation
    print("\n[TEST 6] Dataset generation")

    dataset = injector.generate_dataset([
        translation,
        "The delivery will arrive tomorrow."
    ])

    print("Total injected examples:", len(dataset))

    assert len(dataset) > 0

    # Save generated examples.
    with open(
        "outputs/injected_dataset.jsonl",
        "w",
        encoding="utf-8"
    ) as file:

        for record in dataset:
            file.write(
                json.dumps(record, ensure_ascii=False) + "\n"
            )

    print("\nSaved: outputs/injected_dataset.jsonl")

    print("\n" + "=" * 60)
    print("ALL ERROR INJECTION TESTS PASSED!")
    print("=" * 60)


if __name__ == "__main__":
    main()