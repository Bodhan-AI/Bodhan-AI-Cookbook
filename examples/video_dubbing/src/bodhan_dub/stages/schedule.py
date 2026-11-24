"""
Timeline scheduler for dubbed audio segments.
Places synthesized segments onto the audio timeline, handles start shifts,
enforces the ±300ms sync bound, and eliminates segment overlaps without cascading drift.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
from pydantic import BaseModel, Field

from bodhan_dub.logging import PipelineLogger
from bodhan_dub.media.audio_proc import time_stretch_audio
from bodhan_dub.stages.fit import SegmentFitResult
from bodhan_dub.stages.segment import SegmentSlot


class ScheduledSegment(BaseModel):
    segment_id: str
    source_start: float
    source_end: float
    placed_start: float
    placed_end: float
    sync_offset_ms: float
    start_shift_s: float
    speed_factor: float
    qa_status: str
    fit_status: str
    is_skipped: bool = False
    drift_exceeded: bool = False
    overlap_forced: bool = False
    reasons: List[str] = Field(default_factory=list)


class ScheduleResult(BaseModel):
    lang: str
    total_segments: int
    placed_segments: int
    skipped_segments: int
    degraded_segments: int
    max_sync_offset_ms: float
    p95_sync_offset_ms: float
    stem_duration_s: float = 0.0
    segments: List[ScheduledSegment] = Field(default_factory=list)


class TimelineScheduler:
    """
    Schedules fitted audio segments onto the master timeline.
    Enforces sample-accurate placement without cascading drift or compound stretching.
    """

    def __init__(self, sample_rate: int = 48000, max_sync_drift_s: float = 0.300):
        self.sample_rate = sample_rate
        self.max_sync_drift_s = max_sync_drift_s

    def schedule(
        self,
        lang: str,
        source_segments: List[SegmentSlot],
        fit_results: List[SegmentFitResult],
        audio_buffers: List[Optional[np.ndarray]],
        total_duration_s: float,
        logger: PipelineLogger,
    ) -> Tuple[ScheduleResult, np.ndarray]:
        """
        B6 & B13: Places segments on the 48kHz audio timeline.
        Enforces non-cascading drift, single-stretch from raw synthesis,
        bounded overlap resolution (borrow, stretch <= 1.20x, crossfade <= 100ms),
        and ghost speech prevention.
        Returns (ScheduleResult, speech_stem_audio_48k).
        """
        total_samples = int(round(total_duration_s * self.sample_rate))
        master_buffer = np.zeros(total_samples, dtype=np.float32)

        scheduled: List[ScheduledSegment] = []
        last_placed_end = 0.0
        sync_offsets: List[float] = []

        # Map fit results and buffers by segment_id
        fit_map = {res.segment_id: (res, buf) for res, buf in zip(fit_results, audio_buffers)}

        for idx, seg in enumerate(source_segments):
            if seg.segment_id not in fit_map:
                continue

            fit_res, audio_buf = fit_map[seg.segment_id]

            if fit_res.fit_status == "skipped" or audio_buf is None or len(audio_buf) == 0:
                # Slot is skipped; marked for keeping original audio
                sched = ScheduledSegment(
                    segment_id=seg.segment_id,
                    source_start=seg.start,
                    source_end=seg.end,
                    placed_start=seg.start,
                    placed_end=seg.end,
                    sync_offset_ms=0.0,
                    start_shift_s=0.0,
                    speed_factor=1.0,
                    qa_status=fit_res.qa_status,
                    fit_status="skipped",
                    is_skipped=True,
                )
                scheduled.append(sched)
                continue

            # A1: Maintain local adjusted state; NEVER mutate caller's fit_res
            curr_speed_factor = float(fit_res.speed_factor)
            curr_qa_status = str(fit_res.qa_status)
            curr_fit_status = str(fit_res.fit_status)
            curr_reasons = list(fit_res.reasons)

            # B6: Always re-stretch from raw unstretched audio if available
            raw_audio = getattr(fit_res, "raw_audio", None)
            if raw_audio is None or len(raw_audio) == 0:
                raw_audio = audio_buf.copy()

            # B13: Per-segment sync budget: target start time is source_segment.start
            desired_shift = fit_res.start_shift
            target_start = seg.start + desired_shift
            clamped_start = max(
                seg.start - self.max_sync_drift_s,
                min(seg.start + self.max_sync_drift_s, target_start),
            )

            # A1: Enforce NO overlap with previous placed segment
            drift_exceeded = False
            if last_placed_end > clamped_start:
                max_allowed_start = seg.start + self.max_sync_drift_s
                if last_placed_end <= max_allowed_start:
                    placed_start = last_placed_end
                else:
                    # Overrun beyond the +/-300 ms window: place at last_placed_end, mark overrun & degraded
                    placed_start = last_placed_end
                    drift_exceeded = True
                    curr_fit_status = "overrun"
                    curr_qa_status = "degraded"
                    if "drift_exceeded" not in curr_reasons:
                        curr_reasons.append("drift_exceeded")
            else:
                placed_start = clamped_start

            placed_start = max(0.0, placed_start)
            dur_s = len(audio_buf) / float(self.sample_rate)

            # Lookahead: next valid segment start boundary
            next_seg_target_start = total_duration_s
            for next_seg in source_segments[idx + 1:]:
                if next_seg.segment_id in fit_map and fit_map[next_seg.segment_id][0].fit_status != "skipped":
                    next_seg_target_start = next_seg.start
                    break

            # A1: Resolution order when a segment would run into the next one:
            # 1. Borrow leading slack (<=250 ms, not before previous placed end)
            # 2. Re-stretch from the TRUE raw audio (<=1.2x)
            # 3. Shift within +/-300 ms sync window
            # 4. If still over, mark overrun and qa_status=degraded with a reason
            if placed_start + dur_s > next_seg_target_start:
                # 1. Borrow leading slack
                earliest_possible = max(last_placed_end, seg.start - min(0.250, self.max_sync_drift_s))
                shift_avail = max(0.0, placed_start - earliest_possible)
                if shift_avail > 0:
                    needed_shift = min(shift_avail, (placed_start + dur_s) - next_seg_target_start)
                    placed_start -= needed_shift

                # 2. Re-stretch from raw audio (<= 1.20x)
                if placed_start + dur_s > next_seg_target_start:
                    avail_time = max(0.1, next_seg_target_start - placed_start)
                    raw_dur_s = len(raw_audio) / float(self.sample_rate)
                    required_speed = raw_dur_s / avail_time
                    capped_speed = min(1.20, max(1.0, required_speed))
                    if capped_speed > curr_speed_factor:
                        audio_buf = time_stretch_audio(raw_audio, capped_speed, sample_rate=self.sample_rate)
                        dur_s = len(audio_buf) / float(self.sample_rate)
                        curr_speed_factor = round(capped_speed, 3)

                # 3. Shift earlier within full +/-300ms sync window if slack allows
                if placed_start + dur_s > next_seg_target_start:
                    earliest_300 = max(last_placed_end, seg.start - self.max_sync_drift_s)
                    shift_avail_300 = max(0.0, placed_start - earliest_300)
                    if shift_avail_300 > 0:
                        needed = min(shift_avail_300, (placed_start + dur_s) - next_seg_target_start)
                        placed_start -= needed

                # 4. If still over, mark overrun and qa_status=degraded
                if placed_start + dur_s > next_seg_target_start:
                    gap_overrun = (placed_start + dur_s) - next_seg_target_start
                    curr_fit_status = "overrun"
                    curr_qa_status = "degraded"
                    overrun_msg = f"overrun_{gap_overrun*1000.0:.1f}ms"
                    if overrun_msg not in curr_reasons:
                        curr_reasons.append(overrun_msg)
                    logger.warning(
                        f"Segment {seg.segment_id} overruns by {gap_overrun*1000.0:.1f}ms into following slot",
                        stage="schedule",
                        lang=lang,
                        segment_id=seg.segment_id,
                    )

            # Check ghost speech limit
            max_allowed_end = seg.end + self.max_sync_drift_s + 0.100
            if (placed_start + dur_s) > max_allowed_end:
                drift_exceeded = True
                if "drift_exceeded" not in curr_reasons:
                    curr_reasons.append("drift_exceeded")
                logger.warning(
                    f"Segment {seg.segment_id} exceeds VAD speech window ({placed_start+dur_s:.3f}s > {max_allowed_end:.3f}s); flagged drift_exceeded",
                    stage="schedule",
                    lang=lang,
                    segment_id=seg.segment_id,
                )

            # A1: Media end boundary: never slice tail of speech.
            # Pull earlier within allowed shift; if still cannot fit, mark degraded with truncation_risk
            # and pad master_buffer so no speech is cut.
            placed_end = placed_start + dur_s
            if placed_end > total_duration_s:
                excess_s = placed_end - total_duration_s
                earliest_allowed = max(last_placed_end, seg.start - min(0.250, self.max_sync_drift_s))
                shift_avail = placed_start - earliest_allowed
                if shift_avail >= excess_s:
                    placed_start -= excess_s
                    placed_end = placed_start + dur_s
                else:
                    if shift_avail > 0:
                        placed_start -= shift_avail
                        placed_end = placed_start + dur_s
                    curr_qa_status = "degraded"
                    if "truncation_risk" not in curr_reasons:
                        curr_reasons.append("truncation_risk")

            sync_offset_ms = (placed_start - seg.start) * 1000.0
            sync_offsets.append(abs(sync_offset_ms))

            # A1: Direct write with NO overlap (master_buffer is padded if speech extends past media end)
            start_idx = int(round(placed_start * self.sample_rate))
            end_idx = start_idx + len(audio_buf)
            if end_idx > len(master_buffer):
                # Extend master buffer to accommodate speech without truncation
                master_buffer = np.pad(master_buffer, (0, end_idx - len(master_buffer)))
                total_duration_s = len(master_buffer) / float(self.sample_rate)

            # Enforce that start_idx does not overwrite previous placed audio
            start_idx = max(int(round(last_placed_end * self.sample_rate)), start_idx)
            end_idx = start_idx + len(audio_buf)
            if end_idx > len(master_buffer):
                master_buffer = np.pad(master_buffer, (0, end_idx - len(master_buffer)))
                total_duration_s = len(master_buffer) / float(self.sample_rate)

            # Direct assignment: no additive +=, no crossfade overlap
            master_buffer[start_idx:end_idx] = audio_buf
            placed_end = end_idx / float(self.sample_rate)
            last_placed_end = placed_end

            sched = ScheduledSegment(
                segment_id=seg.segment_id,
                source_start=seg.start,
                source_end=seg.end,
                placed_start=round(placed_start, 3),
                placed_end=round(placed_end, 3),
                sync_offset_ms=round(abs(sync_offset_ms), 1),
                start_shift_s=round(placed_start - seg.start, 3),
                speed_factor=round(curr_speed_factor, 3),
                qa_status=curr_qa_status,
                fit_status=curr_fit_status,
                is_skipped=False,
                drift_exceeded=drift_exceeded,
                overlap_forced=False,
                reasons=curr_reasons,
            )
            scheduled.append(sched)

        max_sync = max(sync_offsets) if sync_offsets else 0.0
        p95_sync = float(np.percentile(sync_offsets, 95)) if sync_offsets else 0.0

        placed_cnt = sum(1 for s in scheduled if not s.is_skipped)
        skipped_cnt = sum(1 for s in scheduled if s.is_skipped)
        degraded_cnt = sum(1 for s in scheduled if s.qa_status == "degraded")

        result = ScheduleResult(
            lang=lang,
            total_segments=len(source_segments),
            placed_segments=placed_cnt,
            skipped_segments=skipped_cnt,
            degraded_segments=degraded_cnt,
            max_sync_offset_ms=round(max_sync, 1),
            p95_sync_offset_ms=round(p95_sync, 1),
            stem_duration_s=round(len(master_buffer) / float(self.sample_rate), 3),
            segments=scheduled,
        )

        return result, master_buffer
