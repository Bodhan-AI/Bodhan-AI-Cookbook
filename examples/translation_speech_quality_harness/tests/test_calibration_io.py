"""Calibration labels attach to run items; mismatches warn instead of aborting the run."""

import json
from pathlib import Path

from bodhan_eval.calibration_io import load_calibration_items
from bodhan_eval.dataset import read_dataset
from bodhan_eval.models import Item


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )
    return path


RUN_ITEMS = [
    Item(id="a", lang="hi", source="Water boils at 100 C.", translation="\u0915" * 40),
    Item(id="b", lang="hi", source="Short.", translation="\u0915" * 6),
]


def test_labels_only_file_attaches_labels_to_run_items(tmp_path: Path):
    path = _write(tmp_path / "labels.jsonl", [
        {"id": "a", "lang": "hi", "labels": [{"type": "number", "severity": "major", "span": [30, 38]}]},
        {"id": "b", "lang": "hi", "labels": []},
    ])
    items, warnings = load_calibration_items(path, {"hi"}, RUN_ITEMS)
    assert [item.id for item in items] == ["a", "b"]
    assert items[0].source == "Water boils at 100 C."
    # span 30-38 lies beyond the source length but inside the translation: it must be kept.
    assert items[0].labels[0].type == "number" and items[0].labels[0].span == (30, 38)
    assert items[1].labels == []
    assert warnings == []


def test_unmatched_labels_warn_and_are_ignored(tmp_path: Path):
    path = _write(tmp_path / "labels.jsonl", [
        {"id": "a", "lang": "hi", "labels": []},
        {"id": "zzz", "lang": "hi", "labels": []},
        {"id": "a", "lang": "ta", "labels": []},
    ])
    items, warnings = load_calibration_items(path, {"hi", "ta"}, RUN_ITEMS)
    assert [item.id for item in items] == ["a"]
    assert len(warnings) == 1 and "2" in warnings[0] and "not in the run dataset" in warnings[0]


def test_languages_outside_the_run_are_dropped_silently(tmp_path: Path):
    path = _write(tmp_path / "labels.jsonl", [{"id": "a", "lang": "hi", "labels": []}])
    items, warnings = load_calibration_items(path, {"ta"}, RUN_ITEMS)
    assert items == [] and warnings == []


def test_bad_lines_are_skipped_with_a_warning(tmp_path: Path):
    path = tmp_path / "labels.jsonl"
    path.write_text('not json\n{"id": "a", "lang": "hi", "labels": []}\n[1]\n', encoding="utf-8", newline="\n")
    items, warnings = load_calibration_items(path, {"hi"}, RUN_ITEMS)
    assert [item.id for item in items] == ["a"]
    assert any("skipped 2" in warning for warning in warnings)


def test_dataset_label_span_may_point_into_a_longer_translation(tmp_path: Path):
    row = {
        "id": "x", "lang": "hi", "source": "Short.", "translation": "\u0915" * 30,
        "labels": [{"type": "number", "severity": "major", "span": [20, 28]}],
    }
    result = read_dataset(_write(tmp_path / "d.jsonl", [row]))
    assert result.items[0].labels[0].type == "number"
    assert not any("unmapped_label_span" in warning for warning in result.warnings)


def test_missing_labels_key_does_not_warn(tmp_path: Path):
    row = {"id": "x", "lang": "hi", "source": "Short.", "translation": "\u0915" * 6}
    result = read_dataset(_write(tmp_path / "d.jsonl", [row]))
    assert result.warnings == []
