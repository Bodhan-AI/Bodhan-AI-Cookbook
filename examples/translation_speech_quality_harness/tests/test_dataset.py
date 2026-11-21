import json
from pathlib import Path

from bodhan_eval.dataset import read_dataset
from bodhan_eval.labels import canonical_category


def test_read_dataset_bom_blank_duplicate_and_quarantine(tmp_path: Path):
    path = tmp_path / "data.jsonl"
    path.write_text(
        "\ufeff" + "\n".join([
            json.dumps({"id": "1", "lang": "hi", "source": "Hello"}, ensure_ascii=False),
            "",
            json.dumps({"id": "1", "lang": "hi", "source": "Again"}),
            "{bad json",
            json.dumps({"id": "3", "lang": "xx", "source": "Skip"}),
        ]) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    result = read_dataset(path)
    assert [item.id for item in result.items] == ["1", "1__dup1"]
    assert len(result.quarantined) == 2
    assert result.warnings
    assert result.total_input_lines == 4


def test_labels_lenient_and_extra_fields(tmp_path: Path):
    path = tmp_path / "data.jsonl"
    path.write_text(
        json.dumps({
            "id": "a",
            "lang": "ta",
            "source": "100",
            "translation": "100",
            "labels": [{"type": "number-unit", "severity": "high", "span": [0, 3]}],
            "extra": 1,
        }) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    result = read_dataset(path)
    assert result.items[0].raw["extra"] == 1
    assert canonical_category(result.items[0].labels[0].type, "text") == "number"


def test_language_filtered_rows_are_retained_as_skipped(tmp_path: Path):
    path = tmp_path / "data.jsonl"
    path.write_text(
        json.dumps({"id": "a", "lang": "ta", "source": "hello", "translation": "வணக்கம்"}) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    result = read_dataset(path, {"hi"})
    assert result.total_input_lines == 1
    assert result.skipped[0].reason == "language_filtered:ta"
