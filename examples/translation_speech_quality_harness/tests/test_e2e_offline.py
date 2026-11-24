"""End-to-end: the real CLI on 10 recorded items, fully offline (no network, no keys).

Uses tests/fixtures/e2e: real Bodhan + Claude responses recorded by
scripts/record_e2e_fixture.py. Exercises the real clients, cache, every text check, the
judge, combining, calibration and reports. If this fails with steps skipped as
"offline: not in cache", a prompt or request shape changed: re-record the fixture.
"""

import json
import shutil
from pathlib import Path

import pytest

from bodhan_eval import pipeline
from bodhan_eval.cli import build_parser

FIXTURE = Path(__file__).parent / "fixtures" / "e2e"


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-test")  # never used: --offline
    for k in ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_EFFORT"):
        monkeypatch.delenv(k, raising=False)

    def go(name: str):
        cache = tmp_path / f"cache-{name}"
        cache.mkdir()
        shutil.copy(FIXTURE / "cache.sqlite", cache / "cache.sqlite")  # runs never share state
        out = tmp_path / name
        args = build_parser().parse_args([
            "--dataset", str(FIXTURE / "dataset.jsonl"), "--out", str(out),
            "--cache-dir", str(cache), "--offline", "--judge-repeats", "0", "--log-level", "ERROR",
        ])  # fmt: skip
        code = pipeline.run(args)
        return code, out

    return go


def test_offline_run_finds_planted_errors_and_is_deterministic(run):
    code, out = run("a")
    assert code == 0
    summary = json.loads((out / "summary.json").read_text())
    assert summary["items"] == 10
    assert summary["skipped_steps"] == {}, "fixture is stale: re-run scripts/record_e2e_fixture.py"
    assert summary["cost"]["total_inr"] == 0  # everything came from the cache

    cal = json.loads((out / "calibration.json").read_text())
    tp = sum(v["tp"] for v in cal["metrics"]["per_category"].values())
    assert tp >= 4, cal["metrics"]["per_category"]  # most of the 6 planted errors are found
    for name in ("items.jsonl", "summary.md", "calibration.json", "logs/run.jsonl"):
        assert (out / name).exists()
    md = (out / "summary.md").read_text()
    assert "## Detection accuracy on labelled items" in md

    code2, out2 = run("b")
    assert code2 == 0
    assert (out / "items.jsonl").read_text() == (out2 / "items.jsonl").read_text()
    assert (out / "summary.md").read_text() == (out2 / "summary.md").read_text()
