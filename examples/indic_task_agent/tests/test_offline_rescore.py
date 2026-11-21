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


def test_an_empty_or_mistyped_evidence_folder_is_an_error_not_a_pass(tmp_path):
    (tmp_path / "empty").mkdir()
    for folder in (tmp_path / "empty", tmp_path / "no_such_folder"):
        proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_suite.py"), "--score-only", "--out", str(folder),
                               "--summary-dir", str(tmp_path / "summary")], capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
        assert proc.returncode == 2 and "no task reports found" in proc.stderr
        assert not (tmp_path / "summary" / "summary.json").exists()


def test_the_media_checksum_exception_does_not_excuse_a_missing_text_output(tmp_path):
    import hashlib

    sys.path.insert(0, str(ROOT / "scripts"))
    from run_suite import score
    out = tmp_path / "run"
    out.mkdir()
    digest = hashlib.sha256(b"x").hexdigest()
    (out / "MEDIA.md").write_text(f"| `artifacts/missing.md` | text | `{digest}` |\n| `artifacts/a.mp3` | audio | `{digest}` |\n", encoding="utf-8")
    report = {"status": "completed", "deliverables": [{"path": "artifacts/missing.md", "kind": "translation", "sha256": digest},
                                                      {"path": "artifacts/a.mp3", "kind": "audio", "sha256": digest}],
              "verification": {"all_checked": True}}
    assert score({"expect": {}}, report, out) == ["deliverable file missing: artifacts/missing.md"]


def test_a_refusal_counts_only_when_the_planner_made_it(tmp_path):
    # seen live (gpt-oss-20b, h7): the planner's only plan was rejected, then the run hit the 600 s limit while waiting on the
    # provider's rate limit. Status "failed" with no output matched the impossible task's expectation and scored as a pass.
    sys.path.insert(0, str(ROOT / "scripts"))
    from run_suite import score
    out = tmp_path / "run"
    out.mkdir()
    task = {"expect": {"status": ["failed", "partial", "unsupported"], "no_deliverables": True}}
    for stop in ("time_limit", "step_limit", "interrupted"):
        report = {"status": "failed", "stop_reason": stop, "deliverables": [], "final_answer": "The run stopped before the planner finished.",
                  "verification": {"all_checked": True}}
        assert score(task, report, out) == [f"run stopped by {stop} before the planner finished, so its status is not the planner's answer"]
    refused = {"status": "unsupported", "stop_reason": "finished", "deliverables": [], "final_answer": "Not possible within ₹0.50.",
               "verification": {"all_checked": True}}
    assert score(task, refused, out) == []
