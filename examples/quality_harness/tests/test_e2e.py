"""End-to-end test on the 10-item dataset with FAKE clients: offline, zero-cost, deterministic.

Asserts a full report is produced, known errors are flagged, and two runs sharing a cache
produce byte-identical report files.
"""

import asyncio
from pathlib import Path

from bodhan_eval.cache import Cache
from bodhan_eval.clients.fake import build_fake_clients
from bodhan_eval.cost import CostLedger
from bodhan_eval.dataset import load_dataset
from bodhan_eval.logging_utils import StructuredLogger
from bodhan_eval.pipeline import RunConfig, run_pipeline
from bodhan_eval.report import build_meta, write_reports

DATASET = Path(__file__).resolve().parent.parent / "data" / "tiny10.jsonl"


def _run(out_dir: Path, cache_dir: Path):
    cfg = RunConfig(dataset=str(DATASET), langs=["hi", "ta", "bn"], out=str(out_dir),
                    no_speech=True, no_judge=True)
    cache = Cache(cache_dir)
    ledger = CostLedger()
    rep = load_dataset(str(DATASET), cfg.langs)
    gold = {it.translation: it.source for it in rep.items if it.translation}
    clients = build_fake_clients(cache, ledger, gold)
    logger = StructuredLogger(None, level="error")
    import time
    start = time.time()
    state = asyncio.run(run_pipeline(cfg, clients, None, cache, logger, None, None))
    meta = build_meta(cfg, start, time.time(), state)
    write_reports(str(out_dir), state, cfg, None, None, meta)
    return state


def test_e2e_produces_report_and_flags(tmp_path):
    out = tmp_path / "report"
    state = _run(out, tmp_path / "cache")
    for f in ("summary.md", "summary.json", "items.jsonl", "calibration.json", "meta.json"):
        assert (out / f).exists(), f
    statuses = {r.id: r.status for r in state.results.values()}
    # seeded errors in tiny10
    assert statuses["t02"] == "flagged"  # number change
    assert statuses["t06"] == "flagged"  # untranslated
    # clean items stay clean
    assert statuses["t01"] == "clean"
    assert statuses["t05"] == "clean"


def test_e2e_determinism_from_cache(tmp_path):
    cache = tmp_path / "cache"
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    _run(out_a, cache)
    _run(out_b, cache)
    for f in ("items.jsonl", "summary.json"):
        assert (out_a / f).read_bytes() == (out_b / f).read_bytes(), f


def test_generated_speech_is_actually_checked(tmp_path):
    cfg = RunConfig(dataset=str(DATASET), langs=["hi", "ta", "bn"], out=str(tmp_path),
                    generate=True, no_judge=True, speech_sample_rate=1)
    cache = Cache(tmp_path / "cache")
    clients = build_fake_clients(cache, CostLedger())
    state = asyncio.run(run_pipeline(cfg, clients, None, cache, StructuredLogger(None), None, None))
    assert any("s1_duration" in result.checks_run for result in state.results.values())
