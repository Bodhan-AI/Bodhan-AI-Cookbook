"""Pack VAD speech regions into transcription chunks that only break at pauses.

A chunk is the unit we send to Indic-Transcribe, and its start/end become the
segment's timestamps. Rules, in priority order:

  1. Never longer than `max_len_s` (the API takes at most 30 s; we stay well under
     so the translated speech of one chunk is still "a sentence or two" for TTS).
  2. Cut only in pauses between regions, preferring the longest pause, because a
     long pause is the best acoustic evidence of a sentence end.
  3. Cut early at a clear sentence pause once the chunk is long enough to carry
     context for transcription.
  4. A single region longer than `max_len_s` (nonstop speech) is split at its
     least speech-like frame and both pieces are flagged `forced_cut`.

Pure functions only: no I/O and no API calls, so every rule is unit-tested.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .vad import Region

# Given a window (lo, hi) in seconds, return the best time to cut inside it.
CutFinder = Callable[[float, float], float]


@dataclass(frozen=True)
class ChunkParams:
    max_len_s: float = 15.0
    min_len_s: float = 3.0
    # A pause this long almost always ends a sentence in lecture-style speech.
    sentence_pause_s: float = 0.8


@dataclass(frozen=True)
class Chunk:
    start: float
    end: float
    forced_cut: bool = False  # True if either edge was cut inside continuous speech
    speaker: int = 0  # who speaks it (from voices.plan_speakers); a chunk never spans a speaker turn

    @property
    def duration(self) -> float:
        return self.end - self.start


def midpoint_cut(lo: float, hi: float) -> float:
    return (lo + hi) / 2


def split_long_regions(regions: Sequence[Region], params: ChunkParams,
                       find_cut: CutFinder = midpoint_cut) -> list[tuple[Region, bool]]:
    """Split any region longer than max_len_s. Returns (region, touches_forced_cut) pairs."""
    out: list[tuple[Region, bool]] = []
    for region in regions:
        start, forced = region.start, False
        while region.end - start > params.max_len_s:
            # Cut where both sides stay at least min_len_s long, and the left side fits.
            lo = start + params.min_len_s
            hi = min(start + params.max_len_s, region.end - params.min_len_s)
            cut = find_cut(lo, hi) if hi > lo else start + params.max_len_s
            cut = min(max(cut, start + 1e-3), start + params.max_len_s)
            out.append((Region(start, round(cut, 3)), True))
            start, forced = round(cut, 3), True
        out.append((Region(start, region.end), forced))
    return out


def pack_chunks(regions: Sequence[Region], params: ChunkParams | None = None,
                find_cut: CutFinder = midpoint_cut,
                speaker_of: Callable[[Region], int] | None = None) -> list[Chunk]:
    """`speaker_of` labels a region with its speaker; a change of speaker always ends a chunk."""
    params = params or ChunkParams()
    pieces = split_long_regions(sorted(regions, key=lambda r: r.start), params, find_cut)
    chunks: list[Chunk] = []
    current: list[tuple[Region, bool]] = []
    who = speaker_of or (lambda _region: 0)

    def emit(group: list[tuple[Region, bool]]) -> None:
        talk: dict[int, float] = {}
        for region, _ in group:
            talk[who(region)] = talk.get(who(region), 0.0) + region.duration
        chunks.append(Chunk(group[0][0].start, group[-1][0].end, any(f for _, f in group),
                            speaker=max(talk, key=lambda k: talk[k])))

    for piece in pieces:
        region = piece[0]
        if current:
            gap = region.start - current[-1][0].end
            length_so_far = current[-1][0].end - current[0][0].start
            turn = who(region) != who(current[-1][0])
            if turn or (gap >= params.sentence_pause_s and length_so_far >= params.min_len_s):
                emit(current)
                current = []
        current.append(piece)
        # Too long now: cut at the longest internal pause that leaves a usable head.
        while current[-1][0].end - current[0][0].start > params.max_len_s:
            split_at = _best_split(current, params)
            emit(current[:split_at])
            current = current[split_at:]
    if current:
        emit(current)
    return chunks


def _best_split(group: list[tuple[Region, bool]], params: ChunkParams) -> int:
    """Index i to split group into [:i] and [i:]: longest pause whose head fits max_len_s.

    Prefers heads of at least min_len_s; ties go to the later pause (longer chunk, more context).
    """
    start = group[0][0].start
    best_i, best_key = 1, (False, -1.0, 0)
    for i in range(1, len(group)):
        head_len = group[i - 1][0].end - start
        if head_len > params.max_len_s:
            break
        gap = group[i][0].start - group[i - 1][0].end
        key = (head_len >= params.min_len_s, gap, i)
        if key > best_key:
            best_i, best_key = i, key
    return best_i
