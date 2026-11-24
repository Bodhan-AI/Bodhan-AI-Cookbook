import json
import runpy
from pathlib import Path

import pytest

REVIEW = runpy.run_path(str(Path(__file__).resolve().parent.parent / "scripts" / "human_review.py"))


def test_review_export_never_treats_unreviewed_as_clean(tmp_path):
    dataset = tmp_path / "input.jsonl"
    source = [{"id": "a", "lang": "hi", "source": "Hello", "translation": "नमस्ते"},
              {"id": "b", "lang": "hi", "source": "Hello", "translation": "नमस्ते",
               "labels": [{"type": "number_unit"}]}]
    dataset.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in source), encoding="utf-8")
    template = tmp_path / "review.jsonl"
    assert REVIEW["prepare"](dataset, template, 1) == 2
    tasks = [json.loads(line) for line in template.read_text(encoding="utf-8").splitlines()]
    assert all(row["labels"] is None and row["reviewed"] is False for row in tasks)
    tasks[0].update(reviewed=True, annotator="human reviewer", labels=[])
    template.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in tasks), encoding="utf-8")
    out = tmp_path / "labels.jsonl"
    assert REVIEW["export"](template, out) == 1
    assert json.loads(out.read_text(encoding="utf-8"))["labels"] == []


def test_review_export_rejects_invalid_spans(tmp_path):
    path = tmp_path / "review.jsonl"
    item = {"id": "x", "reviewed": True, "annotator": "human", "source": "abc",
            "translation": "xyz", "labels": [{"type": "format", "span": [0, 5]}]}
    path.write_text(json.dumps(item), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid span"):
        REVIEW["export"](path, tmp_path / "labels.jsonl")
