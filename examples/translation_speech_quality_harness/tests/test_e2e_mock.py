import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

RECIPE = Path(__file__).resolve().parents[1]


def test_e2e_ten_items_mock_deterministic(tmp_path: Path):
    dataset = tmp_path / "data.jsonl"
    rows = [
        {"id": f"{index}", "lang": "hi", "source": f"Source {index}", "translation": f"अनुवाद {index}"}
        for index in range(10)
    ]
    dataset.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )
    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    command = [
        sys.executable,
        "-m",
        "bodhan_eval",
        "--dataset",
        str(dataset),
        "--langs",
        "hi",
        "--out",
    ]
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONPATH": str(RECIPE / "src"),
    }
    first = subprocess.run(command + [str(out1)], cwd=RECIPE, env=env, capture_output=True, text=True)
    second = subprocess.run(command + [str(out2)], cwd=RECIPE, env=env, capture_output=True, text=True)
    assert first.returncode == second.returncode == 0
    summary = json.loads((out1 / "summary.json").read_text(encoding="utf-8"))
    assert summary["accounting"] == {
        "input_lines": 10,
        "scored": 10,
        "skipped": 0,
        "quarantined": 0,
        "failed_checks": 0,
    }
    assert summary["coverage"]["overall"] == 1.0
    for name in ("summary.md", "summary.json", "items.jsonl", "calibration.json"):
        hash1 = hashlib.sha256((out1 / name).read_bytes()).hexdigest()
        hash2 = hashlib.sha256((out2 / name).read_bytes()).hexdigest()
        assert hash1 == hash2


def test_missing_translation_is_skipped_not_clean(tmp_path: Path):
    dataset = tmp_path / "missing.jsonl"
    dataset.write_text(
        json.dumps({"id": "missing", "lang": "hi", "source": "Hello"}) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    out = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "bodhan_eval",
            "--dataset",
            str(dataset),
            "--langs",
            "hi",
            "--out",
            str(out),
        ],
        cwd=RECIPE,
        env={
            **os.environ,
            "PYTHONUTF8": "1",
            "PYTHONPATH": str(RECIPE / "src"),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    record = json.loads((out / "items.jsonl").read_text(encoding="utf-8"))
    assert record["terminal_state"] == "skipped"
    assert record["skipped_reason"] == "missing_translation"
    assert record["flags"] == []


def test_check_failure_has_explicit_terminal_state(monkeypatch):
    from bodhan_eval import pipeline
    from bodhan_eval.budget import Budget
    from bodhan_eval.dataset import DatasetResult
    from bodhan_eval.models import Item

    def fail(_item, _stats):
        raise RuntimeError("synthetic detector failure")

    monkeypatch.setattr(pipeline, "run_text_checks", fail)
    records = pipeline.run_pipeline(
        DatasetResult(items=[Item(id="x", lang="hi", source="Hello", translation="नमस्ते")]),
        Budget(1.0),
    )
    assert records[0]["terminal_state"] == "failed_checks"
    assert records[0]["failed_checks"] == ["text_checks:RuntimeError"]


def test_terminal_state_accounting_includes_filtered_and_failed(monkeypatch, tmp_path):
    from bodhan_eval import pipeline
    from bodhan_eval.budget import Budget
    from bodhan_eval.dataset import read_dataset
    from bodhan_eval.report import write_outputs

    dataset = tmp_path / "mixed.jsonl"
    rows = [
        {"id": "ok", "lang": "hi", "source": "Hello", "translation": "नमस्ते"},
        {"id": "missing", "lang": "hi", "source": "Hello"},
        {"id": "filtered", "lang": "ta", "source": "Hello", "translation": "வணக்கம்"},
    ]
    dataset.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows) + "{bad json\n",
        encoding="utf-8",
        newline="\n",
    )
    result = read_dataset(dataset, {"hi"})

    def fail(_item, _stats):
        raise RuntimeError("synthetic detector failure")

    monkeypatch.setattr(pipeline, "run_text_checks", fail)
    budget = Budget(1.0)
    records = pipeline.run_pipeline(result, budget)
    write_outputs(tmp_path / "out", records, budget, input_lines=result.total_input_lines)
    summary = json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))
    accounting = summary["accounting"]
    assert accounting == {
        "input_lines": 4,
        "scored": 0,
        "skipped": 3,
        "quarantined": 1,
        "failed_checks": 1,
    }
    assert accounting["scored"] + accounting["skipped"] + accounting["quarantined"] == 4
    assert summary["coverage"]["overall"] == 0.0
    assert len(summary["unchecked"]) == 3
