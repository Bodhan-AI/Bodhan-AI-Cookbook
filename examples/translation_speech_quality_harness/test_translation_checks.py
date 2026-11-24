import json

from bodhan_eval.translation_checks import (
    TranslationQualityChecker
)

checker = TranslationQualityChecker()

print("=" * 60)
print("INDICEVAL - TRANSLATION QUALITY EVALUATION")
print("=" * 60)


# Test case 1: Correct translation
print("\n[TEST 1] Correct translation")

result = checker.evaluate(
    item_id="hi_001",
    source="भारत की राजधानी New Delhi है। इसकी जनसंख्या 30 million है।",
    translation="The capital of India is New Delhi. Its population is 30 million.",
    language="hi"
)

print(json.dumps(
    result,
    indent=2,
    ensure_ascii=False
))


# Test case 2: Number changed
print("\n[TEST 2] Number preservation failure")

result = checker.evaluate(
    item_id="hi_002",
    source="इस उत्पाद की कीमत ₹500 है।",
    translation="The price of this product is ₹800.",
    language="hi"
)

print("Overall status:", result["overall_status"])
print("Errors:")

for error in result["errors"]:
    print(
        error["check"],
        "->",
        error["message"]
    )


# Test case 3: Empty translation
print("\n[TEST 3] Empty translation")

result = checker.evaluate(
    item_id="hi_003",
    source="नमस्ते",
    translation="",
    language="hi"
)

print("Overall status:", result["overall_status"])


# Test case 4: Markdown formatting
print("\n[TEST 4] Markdown integrity")

result = checker.evaluate(
    item_id="hi_004",
    source="**Important:** Read this carefully.",
    translation="Important: Read this carefully.",
    language="hi"
)

print("Overall status:", result["overall_status"])


print("\n" + "=" * 60)
print("ALL DEMO TESTS COMPLETED")
print("=" * 60)