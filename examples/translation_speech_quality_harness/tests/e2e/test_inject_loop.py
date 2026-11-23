"""The P5 loop, offline: inject errors into clean items, run the exact command on them, calibrate, score.

No keys are set, so only the tier-0 checks run (script, format, length, reference, numbers); tier-1 checks and
the judge skip with a reason. The loop must still close: labels reach ``calibration.json``, the scorecard
matches the run's own evaluation, the tier-0 categories are caught and clean items stay clean.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from bodhan_eval.inject.core import main as inject_main
from bodhan_eval.score import main as score_main

CLEAN = Path(__file__).resolve().parents[1] / "fixtures" / "clean" / "dataset.jsonl"
TIER0_CATEGORIES = ("format", "number", "wrong_language")


def test_inject_run_calibrate_score(tmp_path: Path) -> None:
    injected = tmp_path / "injected.jsonl"
    assert inject_main(["--dataset", str(CLEAN), "--out", str(injected), "--rate", "0.6", "--seed", "13"]) == 0
    summary = json.loads((tmp_path / "injected.inject.json").read_text("utf-8"))
    assert summary["injected"] >= 8 and summary["clean"] >= 4

    env = {k: v for k, v in os.environ.items() if not k.startswith(("BODHAN_", "LLM_"))}
    cmd = [
        sys.executable, "-m", "bodhan_eval", "--dataset", "injected.jsonl", "--langs", "hi,bn,ta", "--out", "report",
        "--no-speech", "--cache-dir", "cache", "--log-level", "warning",
    ]  # fmt: skip
    proc = subprocess.run(cmd, cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert proc.returncode == 0, proc.stderr

    calibration = json.loads((tmp_path / "report" / "calibration.json").read_text("utf-8"))
    evaluation = calibration["evaluation"]
    assert evaluation["labels"] == summary["injected"]
    assert "cross_validated" in calibration and "tuning" in calibration

    assert score_main(["--report", str(tmp_path / "report"), "--gold", str(injected)]) == 0
    card = json.loads((tmp_path / "report" / "scorecard.json").read_text("utf-8"))
    assert card["headline"]["labels"] == summary["injected"]
    # Without keys there is no back-translation, so a number written as a word ("तीन" for 3) cannot be confirmed
    # and the numbers check reports it as missing (with keys, the back-translation clears it: P2 clean suite).
    # That is the only false alarm tier 0 may raise on these clean items.
    items = [json.loads(x) for x in (tmp_path / "report" / "items.jsonl").read_text("utf-8").splitlines()]
    for f in (f for r in items if r["labels"] == [] for f in r["flags"]):
        assert f["checks"] == ["numbers"] and "missing from the translation" in f["evidence"][0]["detail"], f
    for cat in TIER0_CATEGORIES:
        if cat in summary["per_category"]:
            assert card["per_category"][cat]["recall"] == 1.0, (cat, card["per_category"][cat])
