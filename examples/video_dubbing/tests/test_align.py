"""Pause-level alignment (align.py): pieces between pauses, letter alignment to the full transcript, anchors,
and how sentences use them. No API calls: piece transcripts are given or faked."""

import json
import random

import numpy as np
import soundfile as sf

from bodhan_dub import align
from bodhan_dub.align import align_chunks, build_pieces, find_anchors
from bodhan_dub.chunking import Chunk
from bodhan_dub.sentences import ABBREVIATIONS, anchor_segments, build_sentences, snap_stats

# Real case (maths_variant chunk 5). The piece texts are what Indic-Transcribe returned for each region on its own.
MV_REGIONS = [[46.44, 48.23], [48.68, 50.96], [51.52, 53.4], [53.7, 54.96], [55.2, 55.98]]
MV_TEXT = "Here the points A, O, and B are collinear, and the point C is not. Write the definition and draw the figure."
MV_PIECES = ["HERE THE POINTS A", "O AND B ARE COLLINEAR", "AND THE POINT C IS NOT", "Right the definition",
             "and draw the figure"]


def _chunk(regions, text, anchors=None, start=None, end=None):
    c = {"id": 1, "start": regions[0][0] if start is None else start, "end": regions[-1][1] if end is None else end,
         "regions": regions, "text": text}
    if anchors is not None:
        c["anchors"] = anchors
    return c


# ---------------------------------------------------------------- pieces
def test_pieces_tile_the_chunk_mid_pause_to_mid_pause():
    ps = build_pieces(MV_REGIONS, 46.25, 56.25)
    assert [(p["r0"], p["r1"]) for p in ps] == [(j, j) for j in range(5)]
    assert ps[0]["t0"] == 46.25 and ps[-1]["t1"] == 56.25
    assert all(a["t1"] == b["t0"] for a, b in zip(ps, ps[1:]))
    assert ps[0]["t1"] == round((48.23 + 48.68) / 2, 3)


def test_tiny_region_joins_neighbour_across_the_shorter_pause():
    regs = [[0.0, 2.0], [2.8, 3.0], [3.2, 5.0]]           # 0.2 s region: pause 0.8 before it, 0.2 after
    ps = build_pieces(regs, 0.0, 5.0)
    assert [(p["r0"], p["r1"]) for p in ps] == [(0, 0), (1, 2)]


def test_all_regions_tiny_gives_one_piece():
    ps = build_pieces([[0.0, 0.1], [0.5, 0.6]], 0.0, 1.0)
    assert len(ps) == 1 and (ps[0]["r0"], ps[0]["r1"]) == (0, 1)


# ---------------------------------------------------------------- anchors
def test_real_case_anchors_every_pause_despite_write_right():
    res = find_anchors(MV_TEXT.split(), MV_PIECES, [0, 1, 2, 3, 4])
    assert res["anchors"] == [[4, 0], [9, 1], [15, 2], [18, 3]]
    assert res["match"] > 0.9 and not res["dropped"]


def test_real_case_sentence_starts_where_the_speaker_starts_it():
    old = build_sentences([_chunk(MV_REGIONS, MV_TEXT)])
    anchors = find_anchors(MV_TEXT.split(), MV_PIECES, [0, 1, 2, 3, 4])["anchors"]
    new = build_sentences([_chunk(MV_REGIONS, MV_TEXT, anchors)])
    assert old[1]["text"].startswith("Write") and old[1]["start"] == 51.52      # the old estimate: 2.2 s early
    assert new[1]["text"].startswith("Write") and new[1]["start"] == 53.7
    assert new[0]["end"] == 53.4 and all(s["timing"] == "exact" for s in new)


def test_misspelled_edge_word_still_anchors():
    toks = "First word collinear.".split()
    res = find_anchors(toks, ["First word", "Collinea"], [0, 1])
    assert res["anchors"] == [[2, 0]]


def test_failed_piece_drops_only_its_own_pauses():
    res = find_anchors(MV_TEXT.split(), [MV_PIECES[0], MV_PIECES[1], None, MV_PIECES[3], MV_PIECES[4]],
                       [0, 1, 2, 3, 4])
    assert res["anchors"] == [[4, 0], [18, 3]]
    assert res["dropped"] == {"unmatched_piece": 2}


def test_piece_that_hears_other_words_gets_no_anchor():
    # Real case (maths_demo chunk 6): the last two pieces heard "Hello" / "Angle"; the full chunk heard "Here."
    # "Hello" still shares "He" with "Here", so the pause before it is anchored (correctly: "Here" follows it);
    # "Angle" matches nothing, so the pause before it is not.
    toks = "Second word Reflex. An angle bigger than one eighty degrees is a reflex angle. Here.".split()
    pieces = ["Second word", "Reflex", "An angle bigger than one eighty degrees is a reflex angle", "Hello", "Angle"]
    res = find_anchors(toks, pieces, [0, 1, 2, 3, 4])
    assert res["anchors"] == [[2, 0], [3, 1], [14, 2]]
    assert res["dropped"] == {"unmatched_piece": 1}
    res = find_anchors(toks, pieces[:3] + ["Thank you"], [0, 1, 2, 4])
    assert res["anchors"] == [[2, 0], [3, 1]] and res["dropped"] == {"unmatched_piece": 1}


def test_junk_pieces_under_music_do_not_block_clean_ones():
    # Real case (nasa_mackie chunk 1, loud music): two pieces came back as a run of one Oriya sign.
    toks = ("Hey everyone I m Anthony Mackie and I m hosting a National Geographic special that uncovers the "
            "delicate balance between humans, sharks, and the ocean. Now I know NASA sends astronauts to space and "
            "explores the cosmos, but what does NASA, a space agency, have to do with sharks?").split()
    junk = "ୄ" * 300
    pieces = ["HEY EVERYONE", "I'M ANTHONY MACKEY AND I'M HOSTING A NATIONAL GEOGRAPHIC SPECIAL", junk,
              "NOW I KNOW NASA SENDS ASTRONAUTS TO SPACE AND EXPLORES THE COSMOS", junk, "HAS TO DO WITH SHARKS"]
    res = find_anchors(toks, pieces, [0, 1, 2, 3, 4, 5])
    assert res["anchors"] == [[2, 0]]
    assert res["dropped"] == {"unmatched_piece": 4}


def test_words_only_the_full_chunk_heard_are_not_guessed():
    toks = "the first part continues here unbelievably wonderful and then the second part".split()
    res = find_anchors(toks, ["the first part continues here", "and then the second part"], [0, 1])
    assert res["anchors"] == [] and res["dropped"] == {"unheard_words": 1}


def test_low_match_gives_no_anchors():
    res = find_anchors("alpha beta gamma delta".split(), ["zzz qqq", "xxx www"], [0, 1])
    assert res["anchors"] == [] and res["dropped"] == {"low_match": 1}


def test_invalid_anchors_are_ignored():
    assert anchor_segments(5, 3, [[0, 0], [9, 1], [2, 2], [3, 0]]) == [(0, 3, 0, 1), (3, 5, 1, 3)]


def test_snap_stats_count_aligned_boundaries():
    anchors = find_anchors(MV_TEXT.split(), MV_PIECES, [0, 1, 2, 3, 4])["anchors"]
    st = snap_stats([_chunk(MV_REGIONS, MV_TEXT, anchors)])
    assert st == {"internal_boundaries": 1, "snapped_to_pause": 1, "at_aligned_pause": 1}


# ---------------------------------------------------------------- randomised: speaking rate varies by region
def _fake_chunk(rng: random.Random):
    """Regions with their own words and their own speaking rate, sentences ending at some region ends."""
    t, regions, region_words = 0.0, [], []
    for _ in range(rng.randint(3, 7)):
        words = ["".join(rng.choice("abcdefghijklmnoprstuw") for _ in range(rng.randint(1, 9)))
                 for _ in range(rng.randint(1, 8))]
        rate = rng.uniform(6, 22)                       # letters per second: slow and fast speech in one chunk
        dur = max(0.35, sum(map(len, words)) / rate)
        regions.append([round(t, 3), round(t + dur, 3)])
        region_words.append(words)
        t += dur + rng.uniform(0.25, 1.2)
    ends = [rng.random() < 0.5 for _ in regions]
    ends[-1] = True
    tokens, truth = [], []                            # truth: start time of every sentence after the first
    for j, words in enumerate(region_words):
        if j > 0 and ends[j - 1]:
            truth.append(regions[j][0])
        toks = list(words)
        if ends[j]:
            while toks[-1] + "." in ABBREVIATIONS:     # "no." / "st." are not sentence ends
                toks[-1] += "a"
            toks[-1] += "."
        tokens += toks
    return regions, region_words, tokens, truth


def _corrupt(words: list[str], rng: random.Random) -> str:
    """What an ASR of one short piece might return: edge words misheard, case changed."""
    w = list(words)
    for i in {0, len(w) - 1}:
        if rng.random() < 0.25:
            w[i] = "".join(rng.choice("xyzqv") for _ in range(len(w[i])))
    return " ".join(w).upper() if rng.random() < 0.5 else " ".join(w)


def test_random_chunks_alignment_beats_the_estimate():
    rng = random.Random(7)
    exact_new = exact_old = total = 0
    err_new, err_old, wrong_new = [], [], 0
    for _ in range(300):
        regions, region_words, tokens, truth = _fake_chunk(rng)
        if not truth:
            continue
        text = " ".join(tokens)
        pieces = [_corrupt(ws, rng) for ws in region_words]
        anchors = find_anchors(tokens, pieces, list(range(len(regions))))["anchors"]
        new = [s["start"] for s in build_sentences([_chunk(regions, text, anchors)])][1:]
        old = [s["start"] for s in build_sentences([_chunk(regions, text)])][1:]
        assert len(new) == len(old) == len(truth)
        for a, b, tr in zip(new, old, truth):
            total += 1
            exact_new += abs(a - tr) < 1e-3
            exact_old += abs(b - tr) < 1e-3
            err_new.append(abs(a - tr))
            err_old.append(abs(b - tr))
            wrong_new += abs(a - tr) > 0.3
            assert abs(a - tr) <= abs(b - tr) + 1e-6  # an anchor never makes a start worse than the estimate
    # Measured with seed 7 (606 breaks): exact 0.988 vs 0.743 for the estimate; > 0.3 s off 1.2% vs 25.4%.
    assert total > 200
    assert exact_new / total >= 0.95
    assert exact_new > exact_old
    assert np.mean(err_new) < 0.5 * np.mean(err_old)
    assert wrong_new / total <= 0.03


# ---------------------------------------------------------------- driver: audio pieces, cache, audit
def test_align_chunks_cuts_pieces_and_writes_audit(tmp_path, monkeypatch):
    sr = 16000
    regions = [[0.2, 2.0], [2.6, 4.8], [5.3, 7.0]]
    wav = np.zeros(int(7.5 * sr), dtype="float32")
    p = tmp_path / "chunk_0001.wav"
    sf.write(str(p), wav, sr, subtype="PCM_16")
    ch = Chunk(id=1, start=10.0, end=17.5, speech=5.7, path=str(p), regions=[[a + 10, b + 10] for a, b in regions])
    text = "one two three. four five six seven. eight nine."
    punct = [{"id": 1, "start": 10.0, "end": 17.5, "ok": True, "text": text, "regions": ch.regions}]
    replies = {0: "one two three", 1: "four five six seven", 2: "eight nine"}
    seen = []

    def fake(path, lang, key, cache_dir):
        n = int(path.stem.split("_p")[1])
        seen.append(sf.info(str(path)).duration)
        return {"ok": True, "text": replies[n], "cached": False, "sec": 0.1}

    monkeypatch.setattr(align, "get_key", lambda name: "k")
    out, stats = align_chunks(punct, [ch], tmp_path, transcribe=fake)
    assert out[0]["anchors"] == [[3, 0], [7, 1]]
    assert abs(sum(seen) - 7.5) < 0.01                # pieces tile the whole chunk
    assert stats["pauses"] == 2 and stats["anchors"] == 2 and abs(stats["billed_sec"] - 7.5) < 0.01
    audit = json.loads((tmp_path / "alignment.json").read_text(encoding="utf-8"))
    assert audit[0]["anchors"][1]["next_words"].startswith("eight")
    s = build_sentences(out)
    assert [x["start"] for x in s] == [10.2, 12.6, 15.3]


def test_single_region_chunks_are_not_sent(tmp_path, monkeypatch):
    monkeypatch.setattr(align, "get_key", lambda name: "k")
    ch = Chunk(id=1, start=0.0, end=3.0, speech=2.5, path=str(tmp_path / "x.wav"), regions=[[0.2, 2.7]])
    punct = [{"id": 1, "start": 0.0, "end": 3.0, "ok": True, "text": "hello there.", "regions": ch.regions}]
    out, stats = align_chunks(punct, [ch], tmp_path, transcribe=lambda *a: (_ for _ in ()).throw(AssertionError))
    assert out[0]["anchors"] == [] and stats["pieces"] == 0
