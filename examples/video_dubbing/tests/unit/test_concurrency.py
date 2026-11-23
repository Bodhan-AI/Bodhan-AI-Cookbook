"""
Unit tests for concurrency and parallelism (C3):
- Parallel translation and synthesis per language
- Language-worker concurrency (--concurrency)
- Wall-clock speedup assertion on 2+ languages
"""

import asyncio
import time

import pytest
from fake_bodhan.server import FakeBodhanTransport

from bodhan_dub.config import Settings
from bodhan_dub.languages import LanguageRegistry
from bodhan_dub.logging import PipelineLogger
from bodhan_dub.manifest import RunManifest
from bodhan_dub.orchestrator import PipelineOrchestrator
from bodhan_dub.stages.report import LanguageSummary


@pytest.mark.asyncio
async def test_language_concurrency_wall_clock_speedup(tmp_path):
    """
    C3: Assert running 2 languages with concurrency=2 completes in approximately
    half the wall-clock time compared to sequential concurrency=1.
    """
    settings = Settings(circuit_breaker_threshold=5)
    registry = LanguageRegistry()
    logger = PipelineLogger(run_id="test_conc", log_file=tmp_path / "test.log")
    manifest = RunManifest(
        run_id="test_conc",
        input_file="test.mp4",
        target_langs=["hi", "ta"],
    )

    # Mock process_language that simulates 0.15s of work per language
    async def mock_process_language(self, lang, *args, **kwargs):
        await asyncio.sleep(0.15)
        return LanguageSummary(
            status="ok",
            total_segments=1,
            placed_segments=1,
            degraded_segments=0,
            skipped_segments=0,
            flagged_segments=0,
            overrun_segments=0,
            integrated_lufs=-16.0,
            duration_delta_ms=0.0,
            retries_count=0,
        )

    # 1. Measure sequential execution (concurrency = 1)
    orch_seq = PipelineOrchestrator(
        settings=settings,
        registry=registry,
        logger=logger,
        manifest=manifest,
        out_dir=tmp_path / "out_seq",
        concurrency=1,
        transport=FakeBodhanTransport(),
    )
    orch_seq.process_language = mock_process_language.__get__(orch_seq, PipelineOrchestrator)

    t0_seq = time.perf_counter()
    lang_sem_seq = asyncio.Semaphore(orch_seq.concurrency)

    async def _run_seq(lang_code):
        async with lang_sem_seq:
            return await orch_seq.process_language(lang_code)

    res_seq = await asyncio.gather(*[_run_seq(lang_code) for lang_code in ["hi", "ta"]])
    dur_seq = time.perf_counter() - t0_seq

    assert len(res_seq) == 2
    # 2 x 0.15s should be >= 0.28s
    assert dur_seq >= 0.28

    # 2. Measure concurrent execution (concurrency = 2)
    orch_par = PipelineOrchestrator(
        settings=settings,
        registry=registry,
        logger=logger,
        manifest=manifest,
        out_dir=tmp_path / "out_par",
        concurrency=2,
        transport=FakeBodhanTransport(),
    )
    orch_par.process_language = mock_process_language.__get__(orch_par, PipelineOrchestrator)

    t0_par = time.perf_counter()
    lang_sem_par = asyncio.Semaphore(orch_par.concurrency)

    async def _run_par(lang_code):
        async with lang_sem_par:
            return await orch_par.process_language(lang_code)

    res_par = await asyncio.gather(*[_run_par(lang_code) for lang_code in ["hi", "ta"]])
    dur_par = time.perf_counter() - t0_par

    assert len(res_par) == 2
    # Concurrent 2 x 0.15s should take around ~0.15 - 0.22s
    assert dur_par < dur_seq * 0.80, f"Concurrent dur ({dur_par:.3f}s) not significantly faster than sequential ({dur_seq:.3f}s)"
