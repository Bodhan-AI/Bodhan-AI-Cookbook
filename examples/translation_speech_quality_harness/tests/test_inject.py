import random

from bodhan_eval.inject import build_dataset, inject_text
from bodhan_eval.models import Item

TEXT_CATEGORIES = ("omission", "addition", "mistranslation", "number", "term", "format", "language")


def test_inject_text_seeded_and_span_contains_change():
    item = Item(
        id="x",
        lang="hi",
        source="The price is 100 rupees.",
        translation="कीमत 100 रुपये है। यह दूसरा वाक्य है।",
    )
    for category in TEXT_CATEGORIES:
        first, label1 = inject_text(item, category, random.Random(7))
        second, label2 = inject_text(item, category, random.Random(7))
        assert first == second
        assert label1.span is not None
        if label1.span:
            assert 0 <= label1.span[0] <= label1.span[1] <= len(first)


def test_build_dataset_ratio_and_labels():
    clean = [
        Item(id=str(index), lang="hi", source=f"Source {index}", translation=f"अनुवाद {index}")
        for index in range(20)
    ]
    items = build_dataset(clean, seed=123, error_rate=0.5)
    assert len(items) == 20
    errored = sum(bool(item.labels) for item in items)
    assert 8 <= errored <= 12
    assert all(label.annotator == "injector" for item in items for label in item.labels)


def test_number_injection_changes_native_digits():
    item = Item(id="n", lang="hi", source="१२3", translation="१२3 रुपये")
    changed, label = inject_text(item, "number", random.Random(1))
    assert changed != item.translation
    assert label.extra["method"] == "number_digit_change"


def test_mistranslation_never_inserts_english_text():
    item = Item(
        id="m",
        lang="hi",
        source="The value increases before noon.",
        translation="मान दोपहर से पहले बढ़ता है।",
    )
    changed, label = inject_text(item, "mistranslation", random.Random(5))
    assert "wrong" not in changed
    assert label.extra["method"] in {
        "english_perturbation_retranslate",
        "programmatic_indic_mistranslation",
    }
