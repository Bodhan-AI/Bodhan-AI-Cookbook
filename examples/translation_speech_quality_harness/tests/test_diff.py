import json
from pathlib import Path

from bodhan_eval.diff import compare_runs, diff_markdown


def _write_items(directory: Path, records: list[dict]) -> None:
    directory.mkdir()
    (directory / "items.jsonl").write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
        newline="\n",
    )


def _item(
    item_id: str,
    lang: str = "hi",
    translation: str = "old",
    flags: list[dict] | None = None,
) -> dict:
    return {
        "id": item_id,
        "lang": lang,
        "translation_hash": translation,
        "flags": flags or [],
    }


def test_diff_modified_run_pairs_items_and_reports_changes(tmp_path: Path):
    baseline_dir = tmp_path / "baseline"
    current_dir = tmp_path / "current"
    baseline = [
        _item(
            str(index),
            flags=(
                [{"category": "format", "severity": "minor", "span": [0, 1]}]
                if index == 0
                else []
            ),
        )
        for index in range(10)
    ] + [_item(str(index), lang="ta") for index in range(10, 15)]
    current = [
        _item(
            str(index),
            translation="new",
            flags=(
                [
                    {"category": "format", "severity": "major", "span": [0, 1]},
                    {"category": "number", "severity": "major", "span": [1, 2]},
                ]
                if index == 0
                else [{"category": "number", "severity": "major", "span": [1, 2]}]
            ),
        )
        for index in range(10)
    ] + [_item(str(index), lang="bn") for index in range(20, 25)]
    _write_items(baseline_dir, baseline)
    _write_items(current_dir, current)

    result = compare_runs(baseline_dir, current_dir, seed=19)
    report = diff_markdown(baseline_dir, current_dir, seed=19)

    assert result.regression is True
    assert result.added_items == 5
    assert result.removed_items == 5
    assert result.changed_translations == 10
    assert "New flag: number" in report
    assert "Severity changed: format minor -> major" in report
    assert "paired bootstrap CI95" in report
    assert "Language: bn" in report
    assert "Language: ta" in report
    assert report.count("Example: number") <= 5


def test_identical_runs_have_no_regression(tmp_path: Path):
    run_dir = tmp_path / "run"
    records = [
        _item("1", flags=[{"category": "number", "severity": "minor", "span": [0, 1]}]),
        _item("2"),
    ]
    _write_items(run_dir, records)

    result = compare_runs(run_dir, run_dir, seed=9)
    report = diff_markdown(run_dir, run_dir, seed=9)

    assert result.regression is False
    assert result.changed_translations == 0
    assert "No regressions or changes detected." in report
