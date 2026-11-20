"""Tests for chunk-local to global timeline timestamp remapping (Slice 4)."""

from bodhan_mcp.schemas import AudioChunk, Segment
from bodhan_mcp.utils.audio import remap_timestamps


def test_remap_single_chunk():
    """Verify single chunk at offset 0.0 leaves timestamps unchanged."""
    chunks = [AudioChunk(path="chunk_0.wav", start_s=0.0, end_s=15.0)]
    per_chunk_segments = [
        [
            Segment(start_s=1.2, end_s=4.5, text="Hello"),
            Segment(start_s=5.0, end_s=10.0, text="World"),
        ]
    ]

    remapped = remap_timestamps(chunks, per_chunk_segments)
    assert len(remapped) == 2
    assert remapped[0].start_s == 1.2
    assert remapped[0].end_s == 4.5
    assert remapped[1].start_s == 5.0
    assert remapped[1].end_s == 10.0


def test_remap_multiple_chunks_monotonicity():
    """Verify multiple chunks remap to globally monotonic timestamps without drift."""
    chunks = [
        AudioChunk(path="chunk_0.wav", start_s=0.0, end_s=24.5),
        AudioChunk(path="chunk_1.wav", start_s=24.5, end_s=48.0),
        AudioChunk(path="chunk_2.wav", start_s=48.0, end_s=70.0),
    ]

    per_chunk_segments = [
        [
            Segment(start_s=2.0, end_s=5.0, text="Chunk 0 Part 1"),
            Segment(start_s=10.0, end_s=15.0, text="Chunk 0 Part 2"),
        ],
        [
            Segment(start_s=1.0, end_s=6.0, text="Chunk 1 Part 1"),
            Segment(start_s=12.0, end_s=20.0, text="Chunk 1 Part 2"),
        ],
        [
            Segment(start_s=3.5, end_s=18.0, text="Chunk 2 Part 1"),
        ],
    ]

    remapped = remap_timestamps(chunks, per_chunk_segments)
    assert len(remapped) == 5

    # Check Chunk 0
    assert remapped[0].start_s == 2.0
    assert remapped[0].end_s == 5.0
    assert remapped[1].start_s == 10.0
    assert remapped[1].end_s == 15.0

    # Check Chunk 1 (offset 24.5)
    assert remapped[2].start_s == 25.5  # 24.5 + 1.0
    assert remapped[2].end_s == 30.5    # 24.5 + 6.0
    assert remapped[3].start_s == 36.5  # 24.5 + 12.0
    assert remapped[3].end_s == 44.5    # 24.5 + 20.0

    # Check Chunk 2 (offset 48.0)
    assert remapped[4].start_s == 51.5  # 48.0 + 3.5
    assert remapped[4].end_s == 66.0    # 48.0 + 18.0

    # Verify global ordering is monotonically increasing
    for i in range(len(remapped) - 1):
        assert remapped[i].end_s <= remapped[i + 1].start_s
