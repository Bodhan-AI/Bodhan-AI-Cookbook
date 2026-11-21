from bodhan_eval.budget import Budget
from bodhan_eval.dataset import DatasetResult
from bodhan_eval.models import Item
from bodhan_eval.pipeline import run_pipeline


def _categories(item: Item) -> set[str]:
    records = run_pipeline(DatasetResult(items=[item]), Budget(1.0))
    return {flag["category"] for flag in records[0]["flags"]}


def test_second_independent_error_keeps_the_first_detected_category():
    source = "The notice lists the amount 100 for all visitors."
    first_error = Item(
        "first",
        "hi",
        source,
        "सभी आगंतुकों के लिए राशि 200 है।",
    )
    second_error = Item(
        "second",
        "hi",
        source,
        "सभी आगंतुकों के लिए राशि 200 है। This is untranslated English content.",
    )

    before = _categories(first_error)
    after = _categories(second_error)

    assert "number" in before
    assert "number" in after
    assert "language" in after


def test_contrastive_pairs_have_higher_clean_than_injected_score():
    cases = [
        (
            "number",
            Item("number-clean", "hi", "The code is 42.", "कोड 42 है।"),
            Item("number-bad", "hi", "The code is 42.", "कोड 43 है।"),
        ),
        (
            "format",
            Item(
                "format-clean",
                "hi",
                "A title and list.",
                "# शीर्षक\n- सूची",
                reference="# A title\n- a list",
            ),
            Item(
                "format-bad",
                "hi",
                "A title and list.",
                "शीर्षक\n- सूची",
                reference="# A title\n- a list",
            ),
        ),
        (
            "language",
            Item("language-clean", "hi", "Welcome to the event.", "कार्यक्रम में स्वागत है।"),
            Item("language-bad", "hi", "Welcome to the event.", "Welcome to the event."),
        ),
    ]

    correct_pairs = 0
    for category, clean, injected in cases:
        clean_score = 1 - (category in _categories(clean))
        injected_score = 1 - (category in _categories(injected))
        correct_pairs += clean_score > injected_score

    pairwise_accuracy = correct_pairs / len(cases)
    assert pairwise_accuracy == 1.0
