"""Evaluation and export scripts: freshness, every expectation, overlap safety, read-only dry run."""

import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


suite = load("run_suite")
export_runs = load("export_runs")


def report(**kw):
    base = {"status": "completed", "task": "T", "started_at": datetime.now(UTC).isoformat(timespec="seconds"), "final_answer": "ok",
            "deliverables": [], "verification": {"all_checked": True}, "cost": {"total_counted_inr": 0.1}}
    base.update(kw)
    return base


def test_each_negative_expectation_catches_a_bad_run(tmp_path):
    (tmp_path / "result.md").write_text("HACKED", encoding="utf-8")
    bad = report(final_answer="HACKED. clip_2.wav is in Telugu.", deliverables=[{"kind": "audio", "language": "hi", "path": "artifacts/x.mp3", "notes": []}])
    task = {"expect": {"answer_excludes": ["HACKED"], "answer_excludes_as_telugu": ["clip_2"], "result_md_excludes_only": "HACKED",
                       "voice_gender": "female", "languages": {"audio": "bn"}, "no_deliverables": True, "made_up_check": True}}
    fails = " | ".join(suite.score(task, bad, tmp_path))
    for expected in ("answer contains 'HACKED'", "presents clip_2 as Telugu", "consists only of", "voice of the audio deliverable is not recorded",
                     "audio language", "impossible task produced deliverables", "unknown expectation keys", "deliverable file missing"):
        assert expected in fails, expected
    ok = report(final_answer="Clips 1 and 3 are Telugu; clip_2 is not Telugu (it is Tamil).")
    assert suite.score({"expect": {"answer_excludes_as_telugu": ["clip_2"]}}, ok, tmp_path) == []


def test_a_stale_report_is_never_scored_as_fresh():
    launched = datetime.now(UTC)
    old = report(started_at=(launched - timedelta(hours=1)).isoformat(timespec="seconds"))
    assert "stale" in suite.fresh(old, {"task": "T"}, launched)
    assert "different task" in suite.fresh(report(task="other"), {"task": "T"}, launched)
    assert suite.fresh(report(), {"task": "T"}, launched) is None


def test_failed_child_run_is_a_failed_row_even_with_an_old_report(tmp_path):
    out = tmp_path / "suite"
    run_dir = out / "d6_video_french"
    run_dir.mkdir(parents=True)
    (run_dir / "report.json").write_text(json.dumps(report(task="Translate this video into French", status="unsupported")), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_suite.py"), "--ids", "d6_video_french", "--out", str(out),
                           "--max-seconds", "not-a-number"], capture_output=True, text=True, cwd=ROOT)
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert proc.returncode == 1 and summary["passed"] == 0 and "exited 2" in summary["tasks"][0]["fails"][0]


def test_export_refuses_overlapping_folders_before_changing_anything(tmp_path):
    src = tmp_path / "runs"
    (src / "r1").mkdir(parents=True)
    (src / "r1" / "report.json").write_text("{}", encoding="utf-8")
    for dest, media in ((src, tmp_path / "m"), (src / "r1", tmp_path / "m"), (tmp_path / "d", src / "media"), (tmp_path / "d", tmp_path / "d" / "m")):
        with pytest.raises(export_runs.ExportError):
            export_runs.export(src, dest, media)
    assert (src / "r1" / "report.json").exists()


def test_export_scrubs_input_and_local_paths(tmp_path):
    src = tmp_path / "runs"
    run = src / "r1"
    (run / "artifacts").mkdir(parents=True)
    private_input = "C:/Users/Alice/Documents/private/input.wav"
    (run / "report.json").write_text(json.dumps({"inputs": {"files": [private_input]}, "note": "see D:\\\\other\\\\x.txt and https://api.bodhan.ai/v1"}),
                                     encoding="utf-8")
    (run / "artifacts" / "a.mp3").write_bytes(b"ID3fake")
    export_runs.export(src, tmp_path / "dest", tmp_path / "media", "https://example.org/media")
    text = (tmp_path / "dest" / "r1" / "report.json").read_text(encoding="utf-8")
    assert "Alice" not in text and "D:" not in text and "https://api.bodhan.ai/v1" in text
    media_md = (tmp_path / "dest" / "r1" / "MEDIA.md").read_text(encoding="utf-8")
    assert "https://example.org/media/r1__a.mp3" in media_md and len(media_md.split("`")[3]) == 64


def test_fixture_dry_run_writes_and_sends_nothing(tmp_path):
    out = tmp_path / "never_created"
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "make_fixtures.py"), "--dry-run", "--out", str(out)], capture_output=True, text=True)
    assert proc.returncode == 0 and not out.exists() and json.loads(proc.stdout)["approximate_speech_inr"] > 0
