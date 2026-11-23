"""
Unit tests for TimelineScheduler.
"""

import numpy as np

from bodhan_dub.logging import PipelineLogger
from bodhan_dub.stages.fit import SegmentFitResult
from bodhan_dub.stages.qa import QAMetrics
from bodhan_dub.stages.schedule import TimelineScheduler
from bodhan_dub.stages.segment import Segment


def test_timeline_scheduler_placement():
    logger = PipelineLogger(run_id="test_run")
    scheduler = TimelineScheduler(sample_rate=48000, max_sync_drift_s=0.300)

    # 2 segments: seg1 at [1.0, 3.0], seg2 at [4.0, 6.0]
    seg1 = Segment(
        id="s1",
        index=0,
        start=1.0,
        end=3.0,
        duration=2.0,
        slot=[1.0, 3.0],
        slack_before=0.5,
        slack_after=0.5,
        text="First sentence.",
    )
    seg2 = Segment(
        id="s2",
        index=1,
        start=4.0,
        end=6.0,
        duration=2.0,
        slot=[4.0, 6.0],
        slack_before=0.5,
        slack_after=0.5,
        text="Second sentence.",
    )

    qa1 = QAMetrics(cer=0.02, passed=True)
    qa2 = QAMetrics(cer=0.01, passed=True)

    fit1 = SegmentFitResult(
        segment_id="s1",
        source_en="First sentence.",
        final_en="First sentence.",
        final_translation="पहला वाक्य।",
        raw_duration=2.0,
        final_duration=2.0,
        slot_duration=2.0,
        start_shift=0.0,
        speed_factor=1.0,
        qa_metrics=qa1,
        qa_status="passed",
        fit_status="ok",
        attempts=1,
    )
    fit2 = SegmentFitResult(
        segment_id="s2",
        source_en="Second sentence.",
        final_en="Second sentence.",
        final_translation="दूसरा वाक्य।",
        raw_duration=1.8,
        final_duration=1.8,
        slot_duration=2.0,
        start_shift=-0.1,  # shifted 100ms earlier
        speed_factor=1.0,
        qa_metrics=qa2,
        qa_status="passed",
        fit_status="borrowed_slack",
        attempts=1,
    )

    # Generate 48kHz audio buffers
    buf1 = 0.5 * np.ones(int(2.0 * 48000), dtype=np.float32)
    buf2 = 0.5 * np.ones(int(1.8 * 48000), dtype=np.float32)

    sched_res, master_buffer = scheduler.schedule(
        lang="hi",
        source_segments=[seg1, seg2],
        fit_results=[fit1, fit2],
        audio_buffers=[buf1, buf2],
        total_duration_s=8.0,
        logger=logger,
    )

    assert sched_res.total_segments == 2
    assert sched_res.placed_segments == 2
    assert sched_res.skipped_segments == 0
    assert sched_res.max_sync_offset_ms <= 150.0  # shifted by 100ms
    assert len(master_buffer) == int(8.0 * 48000)

    # Check placement of segment 1
    assert sched_res.segments[0].placed_start == 1.0
    assert sched_res.segments[0].placed_end == 3.0

    # Check placement of segment 2 (target start was 4.0 - 0.1 = 3.9)
    assert sched_res.segments[1].placed_start == 3.9
    assert abs(sched_res.segments[1].placed_end - 5.7) < 0.05


def test_schedule_non_cascading_overrun():
    """B2: An overrun in earlier segment must never cascade drift onto later segments."""
    logger = PipelineLogger(run_id="test_run")
    scheduler = TimelineScheduler(sample_rate=48000, max_sync_drift_s=0.300)

    # seg1 at 1.0..2.0, seg2 at 3.0..4.0
    seg1 = Segment(id="s1", index=0, start=1.0, end=2.0, duration=1.0, slot=[1.0, 2.0], slack_before=0.0, slack_after=0.0, text="First.")
    seg2 = Segment(id="s2", index=1, start=3.0, end=4.0, duration=1.0, slot=[3.0, 4.0], slack_before=0.0, slack_after=0.0, text="Second.")

    # fit1 overran slot (audio 1.3s, ends at 2.3s)
    fit1 = SegmentFitResult(
        segment_id="s1", source_en="First.", final_en="First.", final_translation="पहला",
        raw_duration=1.3, final_duration=1.3, slot_duration=1.0, start_shift=0.0, speed_factor=1.2,
        qa_metrics=QAMetrics(cer=0.01, passed=True), qa_status="passed", fit_status="overrun", attempts=1
    )
    # fit2 is normal 1.0s
    fit2 = SegmentFitResult(
        segment_id="s2", source_en="Second.", final_en="Second.", final_translation="दूसरा",
        raw_duration=1.0, final_duration=1.0, slot_duration=1.0, start_shift=0.0, speed_factor=1.0,
        qa_metrics=QAMetrics(cer=0.01, passed=True), qa_status="passed", fit_status="ok", attempts=1
    )

    buf1 = 0.5 * np.ones(int(1.3 * 48000), dtype=np.float32)
    buf2 = 0.5 * np.ones(int(1.0 * 48000), dtype=np.float32)

    sched_res, master_buffer = scheduler.schedule(
        lang="hi",
        source_segments=[seg1, seg2],
        fit_results=[fit1, fit2],
        audio_buffers=[buf1, buf2],
        total_duration_s=6.0,
        logger=logger,
    )

    # Segment 1 placed at 1.0..2.3
    assert sched_res.segments[0].placed_start == 1.0
    assert sched_res.segments[0].placed_end == 2.3

    # Segment 2 MUST start at its own source start 3.0 (no cascade drift!)
    assert sched_res.segments[1].placed_start == 3.0
    assert sched_res.segments[1].sync_offset_ms == 0.0


def test_schedule_end_of_file_truncation_risk_flagged():
    """B2: Never silently truncate; shift earlier within 250ms limit, or flag truncation_risk and degraded."""
    logger = PipelineLogger(run_id="test_run")
    scheduler = TimelineScheduler(sample_rate=48000, max_sync_drift_s=0.300)

    # Video is 5.0s total. Segment starts at 4.0, ends at 5.0, but audio is 2.5s (cannot fit in 5.0s even with 1.20x stretch)
    seg = Segment(id="s_end", index=0, start=4.0, end=5.0, duration=1.0, slot=[4.0, 5.0], slack_before=0.2, slack_after=0.0, text="Final.")
    fit = SegmentFitResult(
        segment_id="s_end", source_en="Final.", final_en="Final.", final_translation="अंतिम",
        raw_duration=2.5, final_duration=2.5, slot_duration=1.0, start_shift=0.0, speed_factor=1.0,
        qa_metrics=QAMetrics(cer=0.01, passed=True), qa_status="passed", fit_status="ok", attempts=1
    )

    buf = 0.5 * np.ones(int(2.5 * 48000), dtype=np.float32)

    sched_res, master_buffer = scheduler.schedule(
        lang="hi",
        source_segments=[seg],
        fit_results=[fit],
        audio_buffers=[buf],
        total_duration_s=5.0,
        logger=logger,
    )

    # A1: Master buffer is extended to preserve speech without truncation
    assert len(master_buffer) >= int(5.0 * 48000)
    # Flagged truncation_risk and degraded in scheduled segment result (no caller mutation)
    sched_seg = sched_res.segments[0]
    assert sched_seg.qa_status == "degraded"
    assert "truncation_risk" in sched_seg.reasons


def test_schedule_adjacent_tight_segments():
    """B2: Adjacent tight segments (50ms gap) placed without collision."""
    logger = PipelineLogger(run_id="test_run")
    scheduler = TimelineScheduler(sample_rate=48000, max_sync_drift_s=0.300)

    seg1 = Segment(id="s1", index=0, start=1.0, end=2.0, duration=1.0, slot=[1.0, 2.0], slack_before=0.0, slack_after=0.05, text="A")
    seg2 = Segment(id="s2", index=1, start=2.05, end=3.05, duration=1.0, slot=[2.05, 3.05], slack_before=0.05, slack_after=0.0, text="B")

    fit1 = SegmentFitResult(
        segment_id="s1", source_en="A", final_en="A", final_translation="क",
        raw_duration=1.0, final_duration=1.0, slot_duration=1.0, start_shift=0.0, speed_factor=1.0,
        qa_metrics=QAMetrics(cer=0.0, passed=True), qa_status="passed", fit_status="ok", attempts=1
    )
    fit2 = SegmentFitResult(
        segment_id="s2", source_en="B", final_en="B", final_translation="ख",
        raw_duration=1.0, final_duration=1.0, slot_duration=1.0, start_shift=0.0, speed_factor=1.0,
        qa_metrics=QAMetrics(cer=0.0, passed=True), qa_status="passed", fit_status="ok", attempts=1
    )

    buf1 = 0.3 * np.ones(int(1.0 * 48000), dtype=np.float32)
    buf2 = 0.3 * np.ones(int(1.0 * 48000), dtype=np.float32)

    sched_res, master_buffer = scheduler.schedule(
        lang="hi",
        source_segments=[seg1, seg2],
        fit_results=[fit1, fit2],
        audio_buffers=[buf1, buf2],
        total_duration_s=5.0,
        logger=logger,
    )

    # seg1 ends before or at seg2 start (no collision)
    assert sched_res.segments[0].placed_end <= sched_res.segments[1].placed_start
