"""
Sentence segmentation and slot/slack calculation for bodhan_dub.
Deterministic first (sentence boundary merge, length splitting, gap merging),
with optional LLM index-only refinement and validation.
"""

from __future__ import annotations

import re
from typing import Any, List, Optional

import numpy as np
from pydantic import BaseModel, Field

from bodhan_dub.gateway.llm import LLMClient
from bodhan_dub.logging import PipelineLogger
from bodhan_dub.stages.asr import TranscribedSegment


class SegmentSlot(BaseModel):
    id: str
    index: int
    text: str
    start: float
    end: float
    duration: float
    slot: List[float]  # [start, end]
    slack_before: float
    slack_after: float
    max_leading_shift: float = 0.250  # 250ms per spec
    timestamp_source: str = "api_verbose"
    pitch_hz: Optional[float] = None
    assigned_voice: Optional[str] = None

    @property
    def segment_id(self) -> str:
        return self.id

    @property
    def slot_duration(self) -> float:
        return self.duration


Segment = SegmentSlot


class LLMRefinementIndices(BaseModel):
    """LLM provides ONLY index operations to prevent altering English transcript text."""

    merge_indices: List[List[int]] = Field(
        default_factory=list,
        description="List of adjacent segment index pairs to merge, e.g. [[0, 1], [3, 4]]",
    )


def is_sentence_end(text: str) -> bool:
    """Checks if text ends with terminal English punctuation."""
    t = text.strip()
    return bool(t and t[-1] in {".", "!", "?"})


def split_long_sentence_at_clauses(
    seg: TranscribedSegment,
    max_duration: float = 12.0,
) -> List[TranscribedSegment]:
    """
    Splits segments longer than max_duration at clause boundaries (, ; - and, but).
    Allocates timestamps proportionally by character length.
    """
    if seg.duration <= max_duration:
        return [seg]

    text = seg.text.strip()
    # Find clause boundary near middle
    clause_delimiters = [";", ",", " -- ", " - ", " and ", " but ", " because ", " so "]
    split_pos = -1

    midpoint_char = len(text) // 2
    best_dist = float("inf")

    for delim in clause_delimiters:
        matches = [m.start() + len(delim) for m in re.finditer(re.escape(delim), text)]
        for m_idx in matches:
            dist = abs(m_idx - midpoint_char)
            if dist < best_dist and 15 < m_idx < len(text) - 15:
                best_dist = dist
                split_pos = m_idx

    if split_pos > 0:
        first_text = text[:split_pos].strip()
        second_text = text[split_pos:].strip()

        ratio = len(first_text) / float(len(text))
        mid_time = round(seg.start + (seg.duration * ratio), 3)

        first_seg = TranscribedSegment(
            text=first_text,
            start=seg.start,
            end=mid_time,
            timestamp_source=seg.timestamp_source,
        )
        second_seg = TranscribedSegment(
            text=second_text,
            start=mid_time,
            end=seg.end,
            timestamp_source=seg.timestamp_source,
        )
        # Recursively split if still too long
        return split_long_sentence_at_clauses(
            first_seg, max_duration
        ) + split_long_sentence_at_clauses(second_seg, max_duration)

    return [seg]


def compute_slacks(
    segments: List[TranscribedSegment],
    total_audio_duration: Optional[float] = None,
    guard_band: float = 0.080,  # 80ms guard per spec
) -> List[SegmentSlot]:
    """
    Computes slot, slack_before, and slack_after for each segment.
    slack_before = gap to previous segment minus guard_band
    slack_after = gap to next segment minus guard_band
    """
    output: List[SegmentSlot] = []
    n = len(segments)

    for i, seg in enumerate(segments):
        # 1. Slack before
        if i == 0:
            raw_gap_before = seg.start
        else:
            prev_seg = segments[i - 1]
            raw_gap_before = max(0.0, seg.start - prev_seg.end)
        slack_before = max(0.0, raw_gap_before - guard_band)

        # 2. Slack after
        if i == n - 1:
            if total_audio_duration and total_audio_duration > seg.end:
                raw_gap_after = total_audio_duration - seg.end
            else:
                raw_gap_after = 1.0  # reasonable end slack
        else:
            next_seg = segments[i + 1]
            raw_gap_after = max(0.0, next_seg.start - seg.end)
        slack_after = max(0.0, raw_gap_after - guard_band)

        slot = [round(seg.start, 3), round(seg.end, 3)]
        output.append(
            SegmentSlot(
                id=f"seg_{i + 1:04d}",
                index=i,
                text=seg.text,
                start=round(seg.start, 3),
                end=round(seg.end, 3),
                duration=round(seg.duration, 3),
                slot=slot,
                slack_before=round(slack_before, 3),
                slack_after=round(slack_after, 3),
                max_leading_shift=0.250,
                timestamp_source=seg.timestamp_source,
            )
        )

    return output


class Segmenter:
    """Deterministic segmenter with optional LLM refinement."""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        logger: Optional[PipelineLogger] = None,
        max_sentence_duration: float = 12.0,
        min_segment_duration: float = 0.6,
        max_merge_gap: float = 0.400,
    ):
        self.llm_client = llm_client
        self.logger = logger
        self.max_sentence_duration = max_sentence_duration
        self.min_segment_duration = min_segment_duration
        self.max_merge_gap = max_merge_gap

    def segment_deterministic(
        self,
        raw_segments: List[TranscribedSegment],
    ) -> List[TranscribedSegment]:
        """
        Deterministic pass:
        1. Merge fragments that do not end with punctuation if gap is short (<0.8s)
        2. Merge tiny segments (<0.6s) with neighbor if gap <0.4s
        3. Split sentences longer than 12s at clause boundaries
        """
        if not raw_segments:
            return []

        # Step 1: Punctuation and fragment merge
        merged: List[TranscribedSegment] = []
        for seg in raw_segments:
            if not merged:
                merged.append(seg)
                continue

            prev = merged[-1]
            gap = seg.start - prev.end

            # If previous segment didn't end with sentence punctuation and gap is small, merge
            if not is_sentence_end(prev.text) and gap < 0.8:
                prev.text = f"{prev.text} {seg.text}".strip()
                prev.end = max(prev.end, seg.end)
            # If current segment is tiny and gap is small, merge
            elif seg.duration < self.min_segment_duration and gap < self.max_merge_gap:
                prev.text = f"{prev.text} {seg.text}".strip()
                prev.end = max(prev.end, seg.end)
            else:
                merged.append(seg)

        # Step 2: Split long sentences > 12s at clause boundaries
        split_segments: List[TranscribedSegment] = []
        for seg in merged:
            split_segments.extend(split_long_sentence_at_clauses(seg, self.max_sentence_duration))

        return split_segments

    async def refine_with_llm(
        self,
        segments: List[TranscribedSegment],
    ) -> List[TranscribedSegment]:
        """
        Calls LLM for index-only refinement. Validates that text concatenation is unaltered.
        """
        if not self.llm_client or not self.llm_client.settings.llm_api_key or len(segments) <= 1:
            return segments

        # Build prompt listing segments with index and text
        lines = [f"[{i}] {s.text}" for i, s in enumerate(segments)]
        numbered_text = "\n".join(lines)

        merge_indices = await self.llm_client.refine_sentence_segmentation(numbered_text)
        if not merge_indices:
            return segments

        # Apply merge operations on a copy
        try:
            merged_copy = list(segments)
            # Sort merges descending by index to maintain valid indices
            for pair in sorted(merge_indices, key=lambda p: p[0], reverse=True):
                if (
                    len(pair) == 2
                    and 0 <= pair[0] < len(merged_copy) - 1
                    and pair[1] == pair[0] + 1
                ):
                    idx = pair[0]
                    s1 = merged_copy[idx]
                    s2 = merged_copy[idx + 1]
                    merged_seg = TranscribedSegment(
                        text=f"{s1.text} {s2.text}".strip(),
                        start=s1.start,
                        end=s2.end,
                        timestamp_source=s1.timestamp_source,
                    )
                    merged_copy[idx] = merged_seg
                    del merged_copy[idx + 1]

            # Structural validation: Concatenated normalized text must match 100%
            orig_concat = " ".join(s.text.strip() for s in segments)
            new_concat = " ".join(s.text.strip() for s in merged_copy)

            if orig_concat == new_concat:
                if self.logger:
                    self.logger.info(
                        "LLM segmentation refinement accepted (text verified unchanged)",
                        stage="segment",
                    )
                return merged_copy
            else:
                if self.logger:
                    self.logger.warning(
                        "LLM refinement altered transcript text. Discarding refinement.",
                        stage="segment",
                    )
        except Exception as e:
            if self.logger:
                self.logger.warning(
                    f"Error applying LLM refinement: {e}; keeping deterministic result",
                    stage="segment",
                )

        return segments

    async def run(
        self,
        raw_segments: List[TranscribedSegment],
        total_audio_duration: Optional[float] = None,
    ) -> List[SegmentSlot]:
        """
        Runs deterministic segmentation, optional LLM refinement, and computes slacks.
        """
        det_segments = self.segment_deterministic(raw_segments)
        refined_segments = await self.refine_with_llm(det_segments)
        return compute_slacks(refined_segments, total_audio_duration=total_audio_duration)


def estimate_segment_pitch(samples: np.ndarray, sample_rate: int = 16000) -> float:
    """
    C6: Estimates fundamental frequency (F0) in Hz via normalized autocorrelation.
    Human speech F0 search range: 75 Hz to 350 Hz.
    """
    if len(samples) < int(0.1 * sample_rate):
        return 160.0  # default neutral pitch

    frame_len = int(0.040 * sample_rate)  # 40ms frame
    hop_len = int(0.020 * sample_rate)    # 20ms hop
    min_lag = max(1, int(sample_rate / 350.0))  # 350 Hz max
    max_lag = min(frame_len - 1, int(sample_rate / 75.0))  # 75 Hz min

    pitches = []
    for start in range(0, len(samples) - frame_len, hop_len):
        frame = samples[start : start + frame_len]
        energy = float(np.sum(frame**2))
        if energy < 1e-4:
            continue
        corr = np.correlate(frame, frame, mode="full")
        corr = corr[len(frame) - 1 :]
        if max_lag < len(corr):
            search_window = corr[min_lag : max_lag + 1]
            if len(search_window) > 0 and np.max(search_window) > 0:
                peak_idx = min_lag + int(np.argmax(search_window))
                f0 = sample_rate / peak_idx
                pitches.append(f0)

    if not pitches:
        return 160.0
    return float(np.median(pitches))


def cluster_speaker_voices(
    segment_pitches: List[float],
    lang: str,
    registry: Any,
    threshold_hz: float = 35.0,
) -> List[str]:
    """
    C6: Clusters segment pitches into male/female (2 clusters) when --speaker-voices auto is set.
    Maps lower-pitch cluster to male voice, higher-pitch cluster to female voice.
    Falls back to default female voice if clustering fails or single speaker.
    """
    default_voice = registry.get_default_voice(lang, gender="female")
    male_voice = registry.get_default_voice(lang, gender="male")
    female_voice = default_voice

    if not segment_pitches or len(segment_pitches) < 2:
        return [default_voice] * len(segment_pitches)

    min_p = min(segment_pitches)
    max_p = max(segment_pitches)

    # If pitch spread is less than threshold_hz, assume single speaker
    if (max_p - min_p) < threshold_hz:
        med = float(np.median(segment_pitches))
        v = male_voice if med < 150.0 else female_voice
        return [v] * len(segment_pitches)

    # 2-cluster split around midpoint
    midpoint = (min_p + max_p) / 2.0
    assigned_voices = []
    for p in segment_pitches:
        if p < midpoint:
            assigned_voices.append(male_voice)
        else:
            assigned_voices.append(female_voice)
    return assigned_voices
