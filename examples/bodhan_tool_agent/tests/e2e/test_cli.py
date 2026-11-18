"""CLI contract: output layout is always written, even when the task cannot be done."""

import json

from bodhan_agent.__main__ import main


def test_missing_files_end_honestly(tmp_path):
    out = tmp_path / "out"
    code = main(["--task", "Transcribe this", "--files", str(tmp_path / "nope.wav"), "--out", str(out)])
    assert code == 0
    report = json.loads((out / "report.json").read_text())
    assert report["status"] == "impossible"
    assert "missing" in (out / "result.md").read_text()
    assert (out / "artifacts").is_dir()
    lines = (out / "trace.jsonl").read_text().strip().splitlines()
    assert [json.loads(line)["phase"] for line in lines] == ["start", "preflight", "stop"]
    assert list((out / "artifacts").iterdir()) == []
