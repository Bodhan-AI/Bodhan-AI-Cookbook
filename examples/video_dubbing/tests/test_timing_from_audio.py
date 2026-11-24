"""Timing that comes from the audio: VAD edge refinement, region-based word placement, snapping, pause splits."""

import numpy as np

from bodhan_dub.chunking import Chunk, assign_regions
from bodhan_dub.sentences import build_sentences, place_words, snap_boundaries
from bodhan_dub.units import split_sentences
from bodhan_dub.vad import refine_edges

SR = 16000


def _burst_audio(bursts, total, noise_db=-50.0, level=0.3, seed=1):
    rng = np.random.default_rng(seed)
    w = rng.standard_normal(int(total * SR)).astype("float32") * (10 ** (noise_db / 20))
    for a, b in bursts:
        w[int(a * SR) : int(b * SR)] += rng.standard_normal(int(b * SR) - int(a * SR)).astype("float32") * level
    return w


# ---------------------------------------------------------------- VAD refinement
def test_refine_moves_edges_to_real_onset_and_offset():
    w = _burst_audio([(1.0, 2.0), (3.0, 4.5)], 6.0)
    out = refine_edges(w, SR, [(1.08, 1.93), (2.92, 4.6)])
    assert abs(out[0][0] - 1.0) < 0.02 and abs(out[0][1] - 2.0) < 0.02
    assert abs(out[1][0] - 3.0) < 0.02 and abs(out[1][1] - 4.5) < 0.02


def test_refine_works_over_a_music_bed():
    rng = np.random.default_rng(2)
    t = np.arange(int(6 * SR)) / SR
    bed = (0.03 * np.sin(2 * np.pi * 220 * t)).astype("float32")            # steady "music" at -30 dB
    w = bed + _burst_audio([(2.0, 3.0)], 6.0, noise_db=-60)
    out = refine_edges(w, SR, [(2.1, 2.9)])
    assert abs(out[0][0] - 2.0) < 0.03 and abs(out[0][1] - 3.0) < 0.03
    assert rng is not None


def test_refine_keeps_edges_when_not_clearly_above_floor():
    w = _burst_audio([], 3.0, noise_db=-20)
    assert refine_edges(w, SR, [(1.0, 2.0)]) == [(1.0, 2.0)]


def test_refine_never_moves_more_than_search():
    w = _burst_audio([(1.0, 2.0)], 4.0)
    out = refine_edges(w, SR, [(1.4, 1.6)], search=0.15)
    assert out[0][0] >= 1.25 - 1e-6 and out[0][1] <= 1.75 + 1e-6


# ---------------------------------------------------------------- chunk regions
def test_assign_regions_clips_to_chunk_and_sets_edges():
    cs = [Chunk(1, 0.0, 5.0, 4.0, vad_start=0.15, vad_end=4.85), Chunk(2, 5.0, 9.0, 3.0, vad_start=5.0, vad_end=8.85)]
    assign_regions(cs, [(0.2, 2.0), (2.6, 4.8), (4.9, 6.0), (7.0, 8.8)])
    assert cs[0].regions == [[0.2, 2.0], [2.6, 4.8], [4.9, 5.0]]
    assert cs[1].regions == [[5.0, 6.0], [7.0, 8.8]]
    assert cs[0].vad_start == 0.2 and cs[1].vad_end == 8.8


# ---------------------------------------------------------------- word placement and snapping
REGS = [(0.0, 2.0), (2.8, 5.0), (5.6, 9.0)]


def test_place_words_starts_each_region_exactly():
    ws = place_words("aa bb cc dd ee ff gg hh".split(), REGS)
    firsts = [w for w in ws if w["xs"]]
    assert {round(w["start"], 3) for w in firsts} <= {0.0, 2.8, 5.6}
    assert ws[0]["start"] == 0.0 and ws[-1]["end"] == 9.0
    assert all(a["start"] <= b["start"] for a, b in zip(ws, ws[1:]))


def test_snap_boundary_to_real_pause():
    tokens = "One two three four. Five six seven eight nine ten. Eleven twelve thirteen fourteen fifteen.".split()
    chosen = snap_boundaries(tokens, REGS)
    assert chosen == {3: 0, 9: 1}


def test_sentences_start_on_real_pauses():
    chunk = {"id": 1, "start": 0.0, "end": 9.0, "regions": [list(r) for r in REGS],
             "text": "One two three four. Five six seven eight nine ten. Eleven twelve thirteen fourteen fifteen."}
    s = build_sentences([chunk])
    assert [x["start"] for x in s] == [0.0, 2.8, 5.6]
    assert [x["end"] for x in s] == [2.0, 5.0, 9.0]
    assert all(x["timing"] == "exact" for x in s)


def test_no_pause_near_boundary_stays_estimated():
    chunk = {"id": 1, "start": 0.0, "end": 10.0, "regions": [[0.0, 9.0], [9.6, 10.0]],
             "text": "Aaaa aaaa aaaa. Bbbb bbbb bbbb bbbb bbbb bbbb bbbb bbbb bbbb bbbb bbbb."}
    s = build_sentences([chunk])
    assert len(s) == 2 and s[0]["timing"] == "estimated"


def test_old_chunks_without_regions_still_work():
    s = build_sentences([{"id": 1, "start": 0.0, "end": 4.0, "text": "Hello there. General Kenobi."}])
    assert len(s) == 2 and s[0]["start"] == 0.0 and s[1]["end"] == 4.0


# ---------------------------------------------------------------- unit splits at real pauses
def _sentence(words):
    return [{"id": 1, "words": words, "start": words[0]["s"], "end": words[-1]["e"],
             "start_exact": True, "end_exact": True}]


def test_split_at_real_pause_on_clause_boundary():
    w = [{"t": "first", "s": 0.0, "e": 1.0, "xs": True, "xe": False},
         {"t": "part,", "s": 1.0, "e": 2.5, "xs": False, "xe": True},
         {"t": "then", "s": 3.3, "e": 4.0, "xs": True, "xe": False},
         {"t": "second", "s": 4.0, "e": 5.5, "xs": False, "xe": True}]
    u = split_sentences(_sentence(w))
    assert [x["text"] for x in u] == ["first part,", "then second"]
    assert u[1]["start"] == 3.3 and all(x["timing"] == "exact" for x in u)


def test_no_split_at_pause_mid_clause():
    w = [{"t": "the", "s": 0.0, "e": 1.5, "xs": True, "xe": True},
         {"t": "membrane", "s": 2.5, "e": 5.0, "xs": True, "xe": True}]
    u = split_sentences(_sentence(w))
    assert len(u) == 1 and u[0]["inner_pause"] == 1.0


def test_no_split_if_part_too_short():
    w = [{"t": "yes,", "s": 0.0, "e": 0.8, "xs": True, "xe": True},
         {"t": "indeed", "s": 1.6, "e": 4.0, "xs": True, "xe": True}]
    assert len(split_sentences(_sentence(w))) == 1
