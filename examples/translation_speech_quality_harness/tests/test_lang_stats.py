"""Length statistics must be computed per language, never pooled across languages."""

from bodhan_eval.checks import LangStats
from bodhan_eval.dataset import DatasetResult
from bodhan_eval.budget import Budget
from bodhan_eval.lang_stats import compute_lang_stats
from bodhan_eval.models import Item
from bodhan_eval.pipeline import run_pipeline


_CHAR = {"hi": "\u0915", "ta": "\u0b95"}


def _items(lang: str, ratios: list[float], prefix: str) -> list[Item]:
    return [
        Item(
            id=f"{prefix}-{index}",
            lang=lang,
            source="x" * 100,
            translation=_CHAR[lang] * round(100 * ratio),
        )
        for index, ratio in enumerate(ratios)
    ]


def test_each_language_gets_its_own_median():
    items = _items("hi", [1.0] * 10, "hi") + _items("ta", [2.0] * 10, "ta")
    stats = compute_lang_stats(items)
    assert stats["hi"].median_length_ratio == 1.0
    assert stats["ta"].median_length_ratio == 2.0
    assert stats["hi"].length_stats_reliable and stats["ta"].length_stats_reliable


def test_small_language_group_is_marked_unreliable():
    stats = compute_lang_stats(_items("hi", [1.0] * 3, "hi"))
    assert stats["hi"].length_stats_reliable is False


def test_pipeline_does_not_flag_clean_items_of_a_longer_language():
    hi = _items("hi", [1.0, 1.02, 0.98, 1.01, 0.99, 1.0, 1.03, 0.97, 1.0, 1.01], "hi")
    ta = _items("ta", [2.0, 2.04, 1.96, 2.02, 1.98, 2.0, 2.06, 1.94, 2.0, 2.02], "ta")
    result = DatasetResult(items=hi + ta)
    records = run_pipeline(result, Budget(max_inr=0.0))
    assert [r["id"] for r in records if r["flags"]] == []


def test_pipeline_flags_a_within_language_outlier():
    hi = _items("hi", [1.0, 1.02, 0.98, 1.01, 0.99, 1.0, 1.03, 0.97, 1.0, 0.2], "hi")
    ta = _items("ta", [2.0, 2.04, 1.96, 2.02, 1.98, 2.0, 2.06, 1.94, 2.0, 2.02], "ta")
    records = run_pipeline(DatasetResult(items=hi + ta), Budget(max_inr=0.0))
    flagged = {r["id"] for r in records if r["flags"]}
    assert flagged == {"hi-9"}


def test_unreliable_language_is_not_length_checked_and_says_so():
    items = _items("hi", [1.0, 1.0, 1.0, 5.0], "hi")
    records = run_pipeline(DatasetResult(items=items), Budget(max_inr=0.0))
    assert all(not r["flags"] for r in records)
    reasons = [d["reason"] for r in records for d in r["decisions"] if d["check"] == "text_length"]
    assert any("fewer than" in reason for reason in reasons)


def test_langstats_default_is_reliable_for_direct_check_calls():
    assert LangStats().length_stats_reliable is True
