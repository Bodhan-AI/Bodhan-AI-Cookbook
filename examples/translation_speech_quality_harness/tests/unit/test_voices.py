"""Voice comparison (``python -m bodhan_eval.voices``): spec parsing, seeded sample, summary and table."""

from __future__ import annotations

import pytest

from bodhan_eval.models import Item
from bodhan_eval.voices import parse_voices, render, sample, summarise


def test_voice_spec() -> None:
    assert parse_voices("hi=Kavya,Amit; ta=Anitha,Arun") == {"hi": ["Kavya", "Amit"], "ta": ["Anitha", "Arun"]}
    with pytest.raises(ValueError):
        parse_voices("hi=Kavya")  # nothing to compare


def test_sample_is_seeded_and_per_language() -> None:
    items = [Item(id=f"x{k}", lang=lang, source="s", translation="t") for k in range(20) for lang in ("hi", "ta")]
    first = sample(items, "hi", 5, 7)
    assert len(first) == 5 and {it.lang for it in first} == {"hi"} and first == sample(items, "hi", 5, 7)
    assert [it.id for it in first] != [it.id for it in sample(items, "hi", 5, 8)]


def test_summary_pairs_the_same_texts() -> None:
    rows = [
        {"lang": "hi", "voice": "Kavya", "id": "a", "flags": ["speech_omission"], "cer": 0.1, "rate": 14.0},
        {"lang": "hi", "voice": "Kavya", "id": "b", "flags": [], "cer": 0.0, "rate": 15.0},
        {"lang": "hi", "voice": "Amit", "id": "a", "flags": [], "cer": 0.02, "rate": 12.0},
        {"lang": "hi", "voice": "Amit", "id": "b", "flags": [], "cer": 0.0, "rate": 13.0},
    ]
    s = summarise(rows)["hi"]
    assert s["voices"]["Kavya"] == {"items": 2, "flagged_items": 1, "flags": {"speech_omission": 1},
                                    "mean_cer": 0.05, "median_chars_per_second": 14.5}  # fmt: skip
    assert s["paired"] == {"Kavya vs Amit": {"only_Kavya": 1, "only_Amit": 0}}
    assert "| Kavya | 2 | 1 | speech_omission 1 | 5.0% | 14.5 |" in render(summarise(rows))
