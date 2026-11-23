"""
Chunk planner for Bodhan Indic-Transcribe ASR.
Segments long audio into chunks: target 25s, HARD maximum 29s.
Cuts at the pause nearest the target, or falls back to lowest-energy cut with 1.5s overlap.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np
import soundfile as sf

from bodhan_dub.media.vad import Pause, VADResult


@dataclass
class AudioChunk:
    id: int
    start: float
    end: float
    has_overlap: bool = False
    overlap_duration: float = 0.0

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def find_lowest_energy_cut(
    audio: np.ndarray,
    search_start_sample: int,
    search_end_sample: int,
    frame_size: int = 800,  # 50ms at 16kHz
) -> int:
    """Finds sample index with minimal RMS energy within search window."""
    best_idx = (search_start_sample + search_end_sample) // 2
    min_energy = float("inf")

    step = frame_size // 2
    for idx in range(search_start_sample, search_end_sample - frame_size, step):
        frame = audio[idx : idx + frame_size]
        energy = float(np.mean(frame**2))
        if energy < min_energy:
            min_energy = energy
            best_idx = idx + (frame_size // 2)

    return best_idx


class ChunkPlanner:
    """
    Plans bounded audio chunks for ASR.
    - Target chunk duration: 25.0s
    - Hard maximum duration: 29.0s (protects 30s server limit & encoder padding)
    - Fallback cut window: last 8.0s of the 29s window
    - Fallback overlap: 1.5s
    """

    def __init__(
        self,
        target_duration: float = 25.0,
        hard_max_duration: float = 29.0,
        pause_search_window: float = 8.0,
        fallback_overlap: float = 1.5,
    ):
        self.target_duration = target_duration
        self.hard_max_duration = hard_max_duration
        self.pause_search_window = pause_search_window
        self.fallback_overlap = fallback_overlap

    def plan_chunks(
        self,
        vad_result: VADResult,
        audio_path: Optional[Path] = None,
    ) -> List[AudioChunk]:
        """
        Plans chunks based on VAD pauses and total duration.
        If audio_path is provided, uses lowest-energy frame detection for pause-less windows.
        """
        total_duration = vad_result.total_duration
        if total_duration <= 0.0:
            return []

        # If audio is already under hard maximum, single chunk
        if total_duration <= self.hard_max_duration:
            return [AudioChunk(id=0, start=0.0, end=round(total_duration, 3))]

        # Load audio if available for energy calculation
        audio_data: Optional[np.ndarray] = None
        sr = 16000
        if audio_path and Path(audio_path).exists():
            try:
                audio_data, sr = sf.read(str(audio_path))
                if len(audio_data.shape) > 1:
                    audio_data = audio_data[:, 0]
            except Exception:
                audio_data = None

        chunks: List[AudioChunk] = []
        chunk_id = 0
        current_start = 0.0

        while current_start < total_duration:
            # Remaining audio fits in single chunk
            if total_duration - current_start <= self.hard_max_duration:
                chunks.append(
                    AudioChunk(
                        id=chunk_id,
                        start=round(current_start, 3),
                        end=round(total_duration, 3),
                        has_overlap=False,
                    )
                )
                break

            # Search window: from (target - search_window/2) to hard_max
            target_cut = current_start + self.target_duration
            window_min = max(current_start + 10.0, target_cut - (self.pause_search_window / 2))
            window_max = current_start + self.hard_max_duration

            # 1. Look for VAD pauses in the search window
            candidate_pauses: List[Pause] = [
                p for p in vad_result.pauses if (p.end >= window_min and p.start <= window_max)
            ]

            chosen_cut: Optional[float] = None
            is_overlap = False

            if candidate_pauses:
                # Find pause whose midpoint is closest to target_cut
                best_pause = min(
                    candidate_pauses,
                    key=lambda p: abs(((p.start + p.end) / 2.0) - target_cut),
                )
                # Cut at the midpoint of the pause
                midpoint = (best_pause.start + best_pause.end) / 2.0
                # Clamp within hard_max
                chosen_cut = min(midpoint, window_max)

            # 2. Fallback: No pause found in search window -> lowest-energy cut with overlap
            if chosen_cut is None or chosen_cut <= current_start + 5.0:
                is_overlap = True
                if audio_data is not None:
                    search_start_samp = int((window_max - 5.0) * sr)
                    search_end_samp = int(window_max * sr)
                    cut_samp = find_lowest_energy_cut(
                        audio_data, search_start_samp, search_end_samp
                    )
                    chosen_cut = cut_samp / float(sr)
                else:
                    chosen_cut = window_max - 0.5

            # Enforce hard maximum ceiling strictly
            if chosen_cut - current_start > self.hard_max_duration:
                chosen_cut = current_start + self.hard_max_duration

            chunks.append(
                AudioChunk(
                    id=chunk_id,
                    start=round(current_start, 3),
                    end=round(chosen_cut, 3),
                    has_overlap=is_overlap,
                    overlap_duration=self.fallback_overlap if is_overlap else 0.0,
                )
            )

            chunk_id += 1
            # Advance start: if overlap was used, start next chunk (chosen_cut - overlap)
            if is_overlap:
                current_start = max(current_start + 1.0, chosen_cut - self.fallback_overlap)
            else:
                current_start = chosen_cut

        return chunks
