"""End to end, offline: the exact graded command, run as a subprocess against the local stub server.

Fixture (tests/fixtures/e2e): 10 lines = hi/ta/bn items with and without translation, audio, reference
and labels; an unknown language code; a broken JSON line; a missing audio file; a Markdown table +
LaTeX item; plus a calibration file with an id+lang label line and a calibration-only full item.
Audio is generated at test time (never committed).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from bodhan_eval import audio as A
from bodhan_eval.testing.stub_server import SAMPLE_RATE, STUB_CHARS_PER_SECOND, StubServer, speech_like

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "e2e"
REPORT_FILES = ("summary.md", "summary.json", "items.jsonl", "calibration.json")
INDIC = re.compile(r"[ऀ-෿]")
ITEM_KEYS = {
    "id", "lang", "status", "reason", "calibration_only", "generated", "has_reference", "has_translation",
    "audio", "checks", "metrics", "flags", "uncertain", "dismissed", "labels",
}  # fmt: skip


@pytest.fixture(scope="module")
def workdir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Dataset and labels copied next to freshly synthesised audio, so relative audio paths resolve.

    Module-scoped: the CLI run is shared by the read-only tests below (each run takes seconds).
    """
    tmp_path = tmp_path_factory.mktemp("e2e")
    for name in ("dataset.jsonl", "labels.jsonl"):
        shutil.copy(FIXTURES / name, tmp_path / name)
    (tmp_path / "audio").mkdir()
    ta = next(
        json.loads(line) for line in (FIXTURES / "dataset.jsonl").read_text("utf-8").splitlines() if '"ta-01"' in line
    )
    seconds = len(ta["translation"]) / STUB_CHARS_PER_SECOND
    A.write_wav(tmp_path / "audio" / "ta-01.wav", speech_like(seconds), SAMPLE_RATE)
    return tmp_path


@pytest.fixture(scope="module")
def stub() -> Iterator[StubServer]:
    with StubServer(FIXTURES / "stub.json") as s:
        yield s


def run_cli(stub: StubServer, cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """The exact command, with the stub's URLs and placeholder keys; real keys and .env are kept out."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("BODHAN_", "LLM_"))}
    env.update(stub.env())
    cmd = [sys.executable, "-m", "bodhan_eval", *args, "--cache-dir", str(cwd / "cache"), "--log-level", "warning"]
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=300)


def command(out: str, *extra: str) -> list[str]:
    return [
        "--dataset", "dataset.jsonl", "--langs", "hi,ta,bn,xx", "--out", out,
        "--calibration", "labels.jsonl", "--max-cost-inr", "5", "--llm-inr-per-1k-tokens", "0.1", *extra,
    ]  # fmt: skip


def read_items(out: Path) -> dict[str, dict]:
    rows = [json.loads(line) for line in (out / "items.jsonl").read_text("utf-8").splitlines()]
    return {f"{r['id']}::{r['lang']}": r for r in rows}


@pytest.fixture(scope="module")
def first_run(workdir: Path, stub: StubServer) -> Path:
    proc = run_cli(stub, workdir, *command("report1"))
    assert proc.returncode == 0, proc.stderr
    return workdir / "report1"


def test_report_files_and_item_schema(first_run: Path) -> None:
    for name in REPORT_FILES:
        assert (first_run / name).is_file(), name
    assert not (first_run / "diff.md").exists()  # only with --baseline
    items = read_items(first_run)
    assert set(items) == {
        "hi-01::hi", "hi-02::hi", "ta-01::ta", "ta-02::ta", "bn-01::bn",
        "bn-02::bn", "xx-01::xx", "hi-03::hi", "bn-03::bn", "cal-01::hi",
    }  # fmt: skip
    for rec in items.values():
        assert set(rec) == ITEM_KEYS
    assert items["cal-01::hi"]["calibration_only"] is True


def test_bad_lines_skips_and_notes_are_counted(first_run: Path) -> None:
    summary = json.loads((first_run / "summary.json").read_text("utf-8"))
    ds = summary["dataset"]
    assert ds["bad_lines"] == 1
    assert ds["items"] == 9 and ds["skipped_items"] == 1 and ds["calibration_only_items"] == 1
    items = read_items(first_run)
    assert items["ta-02::ta"]["status"] == "skipped" and "nothing to score" in items["ta-02::ta"]["reason"]
    assert items["bn-02::bn"]["audio"] is None
    md = (first_run / "summary.md").read_text("utf-8")
    assert "invalid JSON" in md and "audio file not found" in md


def test_every_skipped_or_failed_check_has_a_reason(first_run: Path) -> None:
    for key, rec in read_items(first_run).items():
        for name, c in rec["checks"].items():
            assert c["status"] in ("ok", "skipped", "error", "not_applicable"), (key, name)
            if c["status"] in ("skipped", "error"):
                assert c.get("reason"), (key, name)
    xx = read_items(first_run)["xx-01::xx"]["checks"]["script"]
    assert xx["status"] == "skipped" and "no script data" in xx["reason"]


def test_flags_have_spans_inside_the_text(first_run: Path) -> None:
    dataset = {
        f"{r['id']}::{r['lang']}": r
        for r in (json.loads(x) for x in (first_run.parent / "dataset.jsonl").read_text("utf-8").splitlines() if x.startswith("{") and x.endswith("}"))
    }  # fmt: skip
    for key, rec in read_items(first_run).items():
        if key not in dataset:
            continue
        tr, src = dataset[key].get("translation") or "", dataset[key]["source"]
        for f in rec["flags"] + rec["uncertain"]:
            if f["span"] is not None:
                assert 0 <= f["span"][0] <= f["span"][1] <= len(tr), (key, f)
                assert f["text"] == (tr[f["span"][0] : f["span"][1]] or None)
            if f["source_span"] is not None:
                assert 0 <= f["source_span"][0] <= f["source_span"][1] <= len(src), (key, f)


def test_planted_errors_are_flagged(first_run: Path) -> None:
    items = read_items(first_run)
    number = [f for f in items["hi-02::hi"]["flags"] if f["category"] == "number"]
    assert number and number[0]["text"] == "8"
    assert any(f["category"] == "wrong_language" for f in items["hi-03::hi"]["flags"])
    assert items["ta-01::ta"]["checks"]["asr"]["status"] == "ok"


def test_llm_prose_never_contains_indic(first_run: Path) -> None:
    judge_details = [
        e["detail"]
        for rec in read_items(first_run).values()
        for f in rec["flags"] + rec["uncertain"]
        for e in f["evidence"]
        if e["check"] == "judge"
    ]
    assert judge_details, "the stub judge flags hi-02, so judge evidence must exist"
    assert not any(INDIC.search(d) for d in judge_details)


def test_reports_contain_no_absolute_paths(first_run: Path) -> None:
    root = str(first_run.parent.resolve())
    for name in REPORT_FILES:
        text = (first_run / name).read_text("utf-8")
        assert root not in text and root.replace("\\", "/") not in text, name


def test_rerun_is_byte_identical_and_free(first_run: Path, workdir: Path, stub: StubServer) -> None:
    proc = run_cli(stub, workdir, *command("report2", "--baseline", "report1"))
    assert proc.returncode == 0, proc.stderr
    second = workdir / "report2"
    for name in REPORT_FILES:
        assert (second / name).read_bytes() == (first_run / name).read_bytes(), name
    meta1 = json.loads((first_run / "run_meta.json").read_text("utf-8"))
    meta2 = json.loads((second / "run_meta.json").read_text("utf-8"))
    assert meta1["spent_inr"]["total"] > 0
    assert meta2["spent_inr"]["total"] == 0 and sum(meta2["paid_calls"].values()) == 0
    assert sum(meta2["cache_hits"].values()) > 0
    diff = (second / "diff.md").read_text("utf-8")
    assert "Regressions (new problems): 0" in diff and "Improvements (problems gone): 0" in diff


def test_transcription_requests_stay_within_30_seconds(first_run: Path, stub: StubServer) -> None:
    asr = [r for r in stub.requests if r["path"] == "/v1/audio/transcriptions" and r["status"] == 200]
    assert asr and all(r["seconds"] <= 30.0 for r in asr)


def test_unreadable_dataset_exits_1(workdir: Path, stub: StubServer) -> None:
    proc = run_cli(stub, workdir, "--dataset", "missing.jsonl", "--langs", "hi", "--out", "r")
    assert proc.returncode == 1
    assert "cannot read dataset" in proc.stderr


def test_generate_fills_translations_and_audio(tmp_path: Path) -> None:
    rows = [{"id": "g-1", "lang": "hi", "source": "Water boils."}, {"id": "g-2", "lang": "bn", "source": "Ice melts."}]
    (tmp_path / "gen.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    fixture = {"translate": {"hi": {"Water boils.": "पानी उबलता है।"}, "bn": {"Ice melts.": "বরফ গলে।"}}}
    with StubServer(fixture) as s:
        proc = run_cli(
            s, tmp_path, "--dataset", "gen.jsonl", "--langs", "hi,bn", "--out", "g", "--generate", "--no-judge"
        )
    assert proc.returncode == 0, proc.stderr
    generated = [json.loads(x) for x in (tmp_path / "g" / "generated.jsonl").read_text("utf-8").splitlines()]
    assert [(g["id"], g["translation"]) for g in generated] == [("g-1", "पानी उबलता है।"), ("g-2", "বরফ গলে।")]
    for g in generated:
        assert g["audio"].startswith("audio/")
        x, sr = A.load_audio(tmp_path / "g" / g["audio"])
        assert sr == SAMPLE_RATE and len(x) > sr // 2
    items = read_items(tmp_path / "g")
    assert all(rec["generated"] == ["audio", "translation"] for rec in items.values())
