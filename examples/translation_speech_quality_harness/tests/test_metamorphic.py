import json
import unicodedata

from bodhan_eval.budget import Budget
from bodhan_eval.dataset import DatasetResult
from bodhan_eval.models import Item
from bodhan_eval.pipeline import run_pipeline


def _categories(items: list[Item]) -> dict[str, list[str]]:
    records = run_pipeline(DatasetResult(items=items), Budget(1.0))
    return {
        record["id"]: sorted({flag["category"] for flag in record["flags"]})
        for record in records
    }


def test_meaning_preserving_text_edits_keep_per_item_flag_categories():
    source = "The notice lists the room number 12 and explains the schedule for all visitors."
    base = "कमरा संख्या 12 है और यह सूचना सभी आगंतुकों के लिए समय बताती है।"
    variants = [
        base,
        unicodedata.normalize("NFD", base),
        base.replace("संख्या", "संख्\u200bया"),
        base.replace(" ", "\u00a0"),
        base.replace(" ", "  "),
        base + "   ",
        base.replace("है और", "है\r\nऔर"),
        base.replace("12", "१२"),
        base.replace("।", "."),
        base.replace("सूचना", "“सूचना”"),
        f"**{base}**",
        "| संख्या | विवरण |\n|---|---|\n| 12 | **कमरा** |",
        base.replace("क", "क़", 1),
    ]
    baseline = _categories([Item("same", "hi", source, variants[0])])["same"]

    for variant in variants[1:]:
        assert _categories([Item("same", "hi", source, variant)])["same"] == baseline


def test_malayalam_legacy_chillu_and_atomic_form_keep_flags():
    source = "The notice describes the location and schedule for the visitors."
    legacy = "സ്ഥലം ണ\u0d4d\u200d പറയുന്ന അറിയിപ്പാണിത്."
    atomic = "സ്ഥലം \u0d7a പറയുന്ന അറിയിപ്പാണിത്."

    assert _categories([Item("legacy", "ml", source, legacy)])["legacy"] == _categories(
        [Item("atomic", "ml", source, atomic)]
    )["atomic"]


def test_order_shuffle_duplicate_and_id_rename_preserve_content_flags():
    first = Item("first", "hi", "Value 100 is ready.", "मूल्य 100 तैयार है।")
    second = Item("second", "hi", "Value 12 is ready.", "मूल्य 13 तैयार है।")
    expected = _categories([first, second])
    shuffled = _categories([second, first])
    renamed = _categories([
        Item("renamed-first", first.lang, first.source, first.translation),
        Item("renamed-second", second.lang, second.source, second.translation),
    ])
    duplicate = _categories([first, second, Item("second__dup", "hi", second.source, second.translation)])

    assert shuffled == expected
    assert renamed == {"renamed-first": expected["first"], "renamed-second": expected["second"]}
    assert duplicate["second__dup"] == expected["second"]
    assert json.dumps(duplicate, sort_keys=True)
