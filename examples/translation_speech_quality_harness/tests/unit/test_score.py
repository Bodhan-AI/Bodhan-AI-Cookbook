"""Scorecard (``python -m bodhan_eval.score``): a report + gold labels -> the numbers a hidden test would give.

A hand-made report and gold file where every number can be worked out by hand (see each assertion).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bodhan_eval.score import main, score


def flag(category: str, span: list[int] | None, source_span: list[int] | None = None) -> dict:
    return {
        "category": category,
        "severity": "major",
        "confidence": 0.9,
        "span": span,
        "source_span": source_span,
        "text": None,
        "source_text": None,
        "evidence": [],
        "checks": ["x"],
    }


def lab(category: str, span: list[int] | None, annotator: str, severity: str = "major") -> dict:
    return {"type": category, "severity": severity, "span": span, "annotator": annotator}


T = "पानी सौ डिग्री पर उबलता है और भाप बनता है।"
GOLD = [
    # A: number error, flagged at the right place with the right category
    {"id": "a", "lang": "hi", "source": "s", "translation": T, "labels": [lab("number", [5, 8], "injector:first_digit")]},
    # B: omission, the harness flags a different category elsewhere; the review queue has it
    {"id": "b", "lang": "hi", "source": "s", "translation": T, "labels": [lab("omission", [0, 0], "injector:drop_phrase", "minor")]},
    # C, D: clean; C gets a false alarm
    {"id": "c", "lang": "ta", "source": "s", "translation": T, "labels": []},
    {"id": "d", "lang": "ta", "source": "s", "translation": T, "labels": []},
    # E: term error, flagged with no span (detected, not localised); a human label
    {"id": "e", "lang": "bn", "source": "s", "translation": T, "labels": [lab("term", [3, 9], "saksham", "critical")]},
    # F: speech omission; the item is missing from the report (counts as missed)
    {"id": "f", "lang": "bn", "source": "s", "translation": T, "labels": [lab("speech_omission", [10, 20], "injector:cut_middle")]},
    # G: unlabelled: not scored
    {"id": "g", "lang": "hi", "source": "s", "translation": T},
]  # fmt: skip
REPORT = [
    {"id": "a", "lang": "hi", "status": "ok", "flags": [flag("number", [5, 8])], "uncertain": []},
    {"id": "b", "lang": "hi", "status": "ok", "flags": [flag("mistranslation", [12, 20])],
     "uncertain": [flag("omission", [0, 3])]},
    {"id": "c", "lang": "ta", "status": "ok", "flags": [flag("format", [0, 4])], "uncertain": []},
    {"id": "d", "lang": "ta", "status": "ok", "flags": [], "uncertain": []},
    {"id": "e", "lang": "bn", "status": "ok", "flags": [flag("term", None)], "uncertain": []},
    {"id": "g", "lang": "hi", "status": "ok", "flags": [flag("number", [0, 2])], "uncertain": []},
]  # fmt: skip


def write(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path]:
    (tmp_path / "report").mkdir()
    write(tmp_path / "report" / "items.jsonl", REPORT)
    return tmp_path / "report", write(tmp_path / "gold.jsonl", GOLD)


def test_headline_numbers(files) -> None:
    card = score(*files)
    h = card["headline"]
    assert h["scored_items"] == 6 and h["error_items"] == 4 and h["clean_items"] == 2
    assert h["labels"] == 4
    assert h["recall"] == 0.5  # a and e of a, b, e, f
    assert h["item_recall"] == 0.75  # a, b (a wrong flag still flags the item), e of a, b, e, f
    assert h["false_alarm_rate"] == 0.5  # c of c, d
    assert h["precision"] == 0.5  # a, e matched; b, c not
    assert h["localization"] == 0.5  # a localised, e detected without a span
    assert h["category_accuracy"] == 1.0
    assert card["coverage"] == {"missing_from_report": 1, "not_ok_in_report": 0, "unlabelled_skipped": 1, "excluded": 0}


def test_review_queue_would_catch_the_missed_omission(files) -> None:
    assert score(*files)["review_queue"] == {"missed_labels": 2, "caught_by_review_queue": 1}


def test_breakdowns_by_category_language_method_and_severity(files) -> None:
    card = score(*files)
    assert card["per_category"]["number"]["recall"] == 1.0
    assert card["per_category"]["omission"]["recall"] == 0.0
    assert card["per_language"]["ta"]["false_alarm_rate"] == 0.5
    assert card["per_language"]["bn"]["recall"] == 0.5
    assert card["per_method"]["injector:first_digit"] == {"labels": 1, "detected": 1, "recall": 1.0}
    assert card["per_method"]["saksham"]["recall"] == 1.0
    assert card["per_method"]["injector:cut_middle"]["recall"] == 0.0
    assert card["per_severity"]["minor"] == {"labels": 1, "detected": 0, "recall": 0.0}
    assert card["per_severity"]["critical"]["recall"] == 1.0
    assert card["matrix"]["hi"]["number"] == {"labels": 1, "detected": 1}


def test_exclude_drops_the_calibration_items(files, tmp_path: Path) -> None:
    excl = write(tmp_path / "calib.jsonl", [{"id": "a", "lang": "hi", "labels": []}, {"id": "c", "lang": "ta"}])
    card = score(*files, exclude=excl)
    assert card["coverage"]["excluded"] == 2
    assert card["headline"]["scored_items"] == 4 and card["headline"]["recall"] == pytest.approx(1 / 3, abs=1e-4)
    assert card["headline"]["false_alarm_rate"] == 0.0


def test_labels_file_labels_items_the_gold_left_unlabelled(files, tmp_path: Path) -> None:
    review = write(tmp_path / "review.jsonl", [{"id": "g", "lang": "hi", "labels": [lab("number", [0, 2], "saksham")]}])
    card = score(*files, labels=[review])
    assert card["coverage"]["unlabelled_skipped"] == 0 and card["headline"]["scored_items"] == 7
    assert card["per_method"]["saksham"] == {"labels": 2, "detected": 2, "recall": 1.0}
    report, gold = files
    assert main(["--report", str(report), "--gold", str(gold), "--labels", str(review), "--out", str(tmp_path / "o")]) == 0


def test_command_writes_deterministic_files(files, tmp_path: Path) -> None:
    report, gold = files
    for out in ("o1", "o2"):
        assert main(["--report", str(report), "--gold", str(gold), "--out", str(tmp_path / out)]) == 0
    for name in ("scorecard.json", "scorecard.md"):
        assert (tmp_path / "o1" / name).read_bytes() == (tmp_path / "o2" / name).read_bytes()
    md = (tmp_path / "o1" / "scorecard.md").read_text(encoding="utf-8")
    assert "| recall | 50.0 % |" in md and "injector:first_digit" in md


def test_command_refuses_missing_report_or_unlabelled_gold(files, tmp_path: Path) -> None:
    report, gold = files
    assert main(["--report", str(tmp_path / "nowhere"), "--gold", str(gold)]) == 1
    bare = write(tmp_path / "bare.jsonl", [{"id": "a", "lang": "hi", "source": "s", "translation": T}])
    assert main(["--report", str(report), "--gold", str(bare)]) == 1
