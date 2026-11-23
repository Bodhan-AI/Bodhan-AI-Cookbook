import json
import os
import subprocess
import sys
from pathlib import Path

RECIPE = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "bodhan_eval", *args],
        cwd=RECIPE,
        env={
            **os.environ,
            "PYTHONUTF8": "1",
            "PYTHONPATH": str(RECIPE / "src"),
        },
        capture_output=True,
        text=True,
    )


def test_help():
    result = run_cli("--help")
    assert result.returncode == 0
    assert "--dataset" in result.stdout
    assert "--max-cost-inr" in result.stdout


def test_missing_required_flag():
    result = run_cli()
    assert result.returncode != 0


def test_fail_on_regression_and_stdout_summary(tmp_path: Path):
    dataset = tmp_path / "data.jsonl"
    dataset.write_text(
        json.dumps(
            {"id": "one", "lang": "hi", "source": "Value 100", "translation": "Value 200"}
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "items.jsonl").write_text(
        json.dumps({"id": "one", "lang": "hi", "flags": []}) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    output = tmp_path / "output"
    result = run_cli(
        "--dataset",
        str(dataset),
        "--langs",
        "hi",
        "--out",
        str(output),
        "--baseline",
        str(baseline),
        "--fail-on-regression",
    )

    assert result.returncode == 2
    assert len(result.stdout.splitlines()) == 1
    assert result.stdout.isascii()
    assert "items=1" in result.stdout
    assert "coverage=1.000000" in result.stdout
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert f"flags={summary['flags']}" in result.stdout
    assert (output / "diff.md").is_file()


def test_calibration_with_unmatched_items_warns_and_still_completes(tmp_path: Path):
    dataset = tmp_path / "run.jsonl"
    dataset.write_text(
        json.dumps(
            {"id": "run-item", "lang": "hi", "source": "Source", "translation": "अनुवाद"}
        ) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    calibration = tmp_path / "labels.jsonl"
    calibration.write_text(
        json.dumps({
            "id": "label-item",
            "lang": "hi",
            "source": "Label source",
            "translation": "लेबल",
            "labels": [{
                "type": "number",
                "severity": "major",
                "span": [0, 1],
                "annotator": "self",
            }],
        }) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    result = run_cli(
        "--dataset",
        str(dataset),
        "--langs",
        "hi",
        "--out",
        str(tmp_path / "report"),
        "--calibration",
        str(calibration),
        "--no-judge",
    )

    assert result.returncode == 0, result.stderr
    calibration_report = json.loads((tmp_path / "report" / "calibration.json").read_text(encoding="utf-8"))
    metrics = calibration_report["metrics"]
    assert metrics["status"] == "insufficient_support"
    assert metrics["reason"] == "no_matching_labeled_items"
    assert any("not in the run dataset" in warning for warning in metrics["warnings"])
