"""A grader can re-score the committed evidence offline: no media, no network, committed summaries untouched."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
D5 = ROOT / "evaluation" / "runs" / "sol_demos_cold" / "d5_bengali_female_audio"


def rescore(runs: Path, summary: Path, code: int = 0) -> dict:
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_suite.py"), "--score-only", "--out", str(runs), "--summary-dir", str(summary)],
                          capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
    assert proc.returncode == code, proc.stderr  # the scorer exits 1 when a task fails
    return json.loads((summary / "summary.json").read_text(encoding="utf-8"))


def test_exported_runs_rescore_from_media_checksums(tmp_path):
    runs = tmp_path / "runs"
    shutil.copytree(D5, runs / D5.name)
    assert not (runs / D5.name / "artifacts" / "paragraph_bengali_female.mp3").exists()  # media is not in git
    s = rescore(runs, tmp_path / "summary")
    assert (s["passed"], s["total"]) == (1, 1) and s["tasks"][0]["evidence"] == "reused"
    assert not (runs / "summary.json").exists()  # the scored folder is left as it was


def test_a_checksum_that_does_not_match_the_report_fails(tmp_path):
    runs = tmp_path / "runs"
    shutil.copytree(D5, runs / D5.name)
    media = runs / D5.name / "MEDIA.md"
    media.write_text(media.read_text(encoding="utf-8").replace("315df1128af784a7", "0000000000000000"), encoding="utf-8")
    s = rescore(runs, tmp_path / "summary", code=1)
    assert s["passed"] == 0 and "deliverable file missing: artifacts/paragraph_bengali_female.mp3" in s["tasks"][0]["fails"]
