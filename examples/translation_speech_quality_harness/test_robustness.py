import json
from pathlib import Path

from bodhan_eval.translation_checks import TranslationQualityChecker


checker = TranslationQualityChecker()


# =========================================================
# ROBUSTNESS TEST DATASET
# =========================================================

TEST_CASES = [

    # -----------------------------------------------------
    # GROUP 1: CLEAN TRANSLATIONS
    # -----------------------------------------------------

    {
        "id": "clean_01",
        "group": "clean",
        "source": "The company received a payment from the customer.",
        "translation": "The company received a payment from the customer."
    },
    {
        "id": "clean_02",
        "group": "clean",
        "source": "The delivery will arrive tomorrow.",
        "translation": "The delivery will arrive tomorrow."
    },
    {
        "id": "clean_03",
        "group": "clean",
        "source": "The product costs Rs 500.",
        "translation": "The product costs Rs 500."
    },
    {
        "id": "clean_04",
        "group": "clean",
        "source": "The meeting starts at 10 AM.",
        "translation": "The meeting starts at 10 AM."
    },
    {
        "id": "clean_05",
        "group": "clean",
        "source": "The customer lives in New Delhi.",
        "translation": "The customer lives in New Delhi."
    },
    {
        "id": "clean_06",
        "group": "clean",
        "source": "The system processes customer requests.",
        "translation": "The system processes customer requests."
    },
    {
        "id": "clean_07",
        "group": "clean",
        "source": "The temperature is 25 degrees.",
        "translation": "The temperature is 25 degrees."
    },
    {
        "id": "clean_08",
        "group": "clean",
        "source": "The company launched a new product.",
        "translation": "The company launched a new product."
    },
    {
        "id": "clean_09",
        "group": "clean",
        "source": "The payment was completed successfully.",
        "translation": "The payment was completed successfully."
    },
    {
        "id": "clean_10",
        "group": "clean",
        "source": "The customer received the confirmation email.",
        "translation": "The customer received the confirmation email."
    },

    # -----------------------------------------------------
    # GROUP 2: LEGITIMATE PARAPHRASES
    # -----------------------------------------------------

    {
        "id": "paraphrase_01",
        "group": "paraphrase",
        "source": "The company received a payment from the customer.",
        "translation": "The firm got a payment from its client."
    },
    {
        "id": "paraphrase_02",
        "group": "paraphrase",
        "source": "The delivery will arrive tomorrow.",
        "translation": "The shipment is expected to reach tomorrow."
    },
    {
        "id": "paraphrase_03",
        "group": "paraphrase",
        "source": "The meeting starts at 10 AM.",
        "translation": "The meeting begins at ten in the morning."
    },
    {
        "id": "paraphrase_04",
        "group": "paraphrase",
        "source": "The company launched a new product.",
        "translation": "The firm introduced a new item."
    },
    {
        "id": "paraphrase_05",
        "group": "paraphrase",
        "source": "The customer received the confirmation email.",
        "translation": "The client got an email confirming the transaction."
    },

    # -----------------------------------------------------
    # GROUP 3: ACTUAL OMISSIONS
    # -----------------------------------------------------

    {
        "id": "omission_01",
        "group": "omission",
        "source": "The company received a payment from the customer.",
        "translation": "The company received a payment."
    },
    {
        "id": "omission_02",
        "group": "omission",
        "source": "The delivery will arrive tomorrow.",
        "translation": "The delivery will arrive."
    },
    {
        "id": "omission_03",
        "group": "omission",
        "source": "The customer paid Rs 500.",
        "translation": "The customer paid."
    },
    {
        "id": "omission_04",
        "group": "omission",
        "source": "The company launched a new product yesterday.",
        "translation": "The company launched a product."
    },
    {
        "id": "omission_05",
        "group": "omission",
        "source": "The payment was completed successfully.",
        "translation": "The payment was completed."
    },

    # -----------------------------------------------------
    # GROUP 4: ACTUAL ADDITIONS
    # -----------------------------------------------------

    {
        "id": "addition_01",
        "group": "addition",
        "source": "The delivery will arrive tomorrow.",
        "translation": "The delivery will arrive tomorrow with free gifts."
    },
    {
        "id": "addition_02",
        "group": "addition",
        "source": "The customer paid Rs 500.",
        "translation": "The customer paid Rs 500 and received a bonus."
    },
    {
        "id": "addition_03",
        "group": "addition",
        "source": "The company launched a new product.",
        "translation": "The company launched a new product worldwide."
    },
    {
        "id": "addition_04",
        "group": "addition",
        "source": "The meeting starts at 10 AM.",
        "translation": "The meeting starts at 10 AM and lasts two hours."
    },
    {
        "id": "addition_05",
        "group": "addition",
        "source": "The payment was completed successfully.",
        "translation": "The payment was completed successfully with no additional fees."
    }
]


# =========================================================
# RUN TESTS
# =========================================================

def run_robustness_tests():

    results = []

    print("=" * 65)
    print("INDICEVAL - ROBUSTNESS TESTING")
    print("=" * 65)

    for case in TEST_CASES:

        result = checker.evaluate(
            item_id=case["id"],
            source=case["source"],
            translation=case["translation"],
            language="en"
        )

        check_results = {
            check["check"]: check["status"]
            for check in result["checks"]
        }

        omission_status = check_results.get(
            "omission_detection"
        )

        addition_status = check_results.get(
            "addition_detection"
        )

        record = {
            "id": case["id"],
            "group": case["group"],
            "source": case["source"],
            "translation": case["translation"],
            "omission_status": omission_status,
            "addition_status": addition_status,
            "overall_status": result["overall_status"]
        }

        results.append(record)

        print(
            f"{case['id']} | "
            f"Group: {case['group']} | "
            f"Omission: {omission_status} | "
            f"Addition: {addition_status}"
        )

    # =====================================================
    # GROUP SUMMARY
    # =====================================================

    print("\n" + "=" * 65)
    print("ROBUSTNESS SUMMARY")
    print("=" * 65)

    groups = [
        "clean",
        "paraphrase",
        "omission",
        "addition"
    ]

    summary = {}

    for group in groups:

        group_results = [
            record
            for record in results
            if record["group"] == group
        ]

        false_omission_flags = sum(
            record["omission_status"] == "warning"
            for record in group_results
        )

        false_addition_flags = sum(
            record["addition_status"] == "warning"
            for record in group_results
        )

        summary[group] = {
            "total": len(group_results),
            "omission_warnings": false_omission_flags,
            "addition_warnings": false_addition_flags
        }

        print(f"\n{group.upper()}")
        print(f"Total cases: {len(group_results)}")
        print(
            f"Omission warnings: {false_omission_flags}"
        )
        print(
            f"Addition warnings: {false_addition_flags}"
        )

    # =====================================================
    # SAVE REPORT
    # =====================================================

    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)

    report = {
        "benchmark": "IndicEval Robustness Test",
        "benchmark_type": "synthetic",
        "total_cases": len(results),
        "group_summary": summary,
        "results": results
    }

    output_path = output_dir / "robustness_report.json"

    with output_path.open("w", encoding="utf-8") as file:

        json.dump(
            report,
            file,
            indent=4,
            ensure_ascii=False
        )

    print("\n" + "=" * 65)
    print("ROBUSTNESS TEST COMPLETED")
    print("=" * 65)

    print(f"Total cases: {len(results)}")
    print(f"Report saved: {output_path}")


if __name__ == "__main__":
    run_robustness_tests()