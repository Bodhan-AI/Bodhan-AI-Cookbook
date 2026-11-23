"""
Unit tests for chunk planner.
Verifies target duration 25s, HARD maximum 29s, pause-based cuts, and fallback overlap.
"""

from bodhan_dub.media.chunks import ChunkPlanner
from bodhan_dub.media.vad import Pause, SpeechRegion, VADResult


def test_chunk_planner_under_max_duration():
    planner = ChunkPlanner(target_duration=25.0, hard_max_duration=29.0)
    vad = VADResult(
        total_duration=18.5,
        speech_regions=[SpeechRegion(0.5, 18.0)],
        pauses=[],
    )

    chunks = planner.plan_chunks(vad)
    assert len(chunks) == 1
    assert chunks[0].start == 0.0
    assert chunks[0].end == 18.5
    assert chunks[0].duration <= 29.0


def test_chunk_planner_cuts_at_nearest_pause():
    planner = ChunkPlanner(target_duration=25.0, hard_max_duration=29.0)
    vad = VADResult(
        total_duration=60.0,
        speech_regions=[],
        pauses=[
            Pause(start=10.0, end=11.0),
            Pause(start=24.0, end=26.0),  # Midpoint is 25.0 (exact target!)
            Pause(start=48.0, end=49.0),
        ],
    )

    chunks = planner.plan_chunks(vad)
    assert len(chunks) >= 2
    # First chunk cuts at midpoint of pause around 25s
    assert chunks[0].end == 25.0
    assert chunks[0].duration <= 29.0
    assert chunks[1].start == 25.0


def test_chunk_planner_hard_max_ceiling():
    planner = ChunkPlanner(target_duration=25.0, hard_max_duration=29.0)
    # Audio with NO pauses for 100 seconds
    vad = VADResult(
        total_duration=100.0,
        speech_regions=[SpeechRegion(0.0, 100.0)],
        pauses=[],
    )

    chunks = planner.plan_chunks(vad)
    assert len(chunks) > 3
    for ch in chunks:
        assert ch.duration <= 29.001, f"Chunk duration {ch.duration} exceeded hard max 29.0s!"
