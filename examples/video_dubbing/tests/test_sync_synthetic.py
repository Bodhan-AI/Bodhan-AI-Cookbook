"""End-to-end sync on synthetic cases with KNOWN timing.

Each case runs the real fit -> render -> placement code with a fake TTS (noise "speech" with real pauses),
builds the dubbed track, and measures it independently with VAD (energy backend; synthetic audio is not
speech-like enough for Silero). Asserts start error < 50 ms and end error < ~250 ms where fitting is possible,
and that impossible cases are flagged instead of hidden.
"""

import subprocess

import numpy as np
import pytest
import soundfile as sf

from bodhan_dub.assemble import build_speech_track
from bodhan_dub.dub_language import Deps, DubConfig, dub_language
from bodhan_dub.fit import END_SLACK
from bodhan_dub.sync_check import measure_sync

SR = 24000


def fake_clip(path, voiced, pauses=(), lead=0.07, tail=0.06, seed=0):
    rng = np.random.default_rng(seed)
    w = [np.zeros(int(SR * lead), "float32")]
    speech = voiced - sum(pauses)
    parts = len(pauses) + 1
    for i in range(parts):
        w.append((rng.standard_normal(int(SR * speech / parts)) * 0.2).astype("float32"))
        if i < len(pauses):
            w.append(np.zeros(int(SR * pauses[i]), "float32"))
    w.append(np.zeros(int(SR * tail), "float32"))
    sf.write(path, np.concatenate(w), SR)


def run_case(tmp_path, units, video_dur, condense_to=None):
    """units: [(start, end, voiced_seconds, pauses_in_clip, inner_pause_in_source)]"""
    plan = {}

    def tts(text, variant):
        voiced, pauses = plan[text]
        p = tmp_path / f"tts_{abs(hash((text, variant)))}.wav"
        fake_clip(p, voiced, pauses)
        return {"ok": True, "path": str(p), "dur": voiced, "chars": len(text), "cached": False}

    def translate(en):
        if condense_to is None:
            return None
        t = "c" * len(en)
        plan[t] = (condense_to, ())
        return t

    segs = []
    for i, (s, e, voiced, pauses, inner) in enumerate(units, 1):
        text = f"seg{i}-" + "x" * i
        plan[text] = (voiced, tuple(pauses))
        segs.append({"id": i, "start": s, "end": e, "translation": text, "src": "English words here for unit",
                     "inner_pause": inner})
    deps = Deps(tts=tts, qa=lambda p, r: None, translate=translate,
                condense=lambda en, t, p, n: (en[: len(en) // 2] if condense_to is not None else None))
    recs, summary = dub_language(segs, video_dur, tmp_path, DubConfig(qa=False, vad_backend="energy"), deps)
    track = build_speech_track(recs, video_dur)
    w24 = tmp_path / "track24.wav"
    sf.write(w24, track, SR)
    w16 = tmp_path / "track16.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(w24), "-ar", "16000", str(w16)], check=True)
    src_regions = [(s, e) for s, e, *_ in units]
    rep = measure_sync(w16, recs, segs, tmp_path / "m", source_regions=src_regions, backend="energy")
    return recs, summary, rep


def errs(rep):
    return {u["id"]: (u["start_err"], u["end_err"]) for u in rep["units"]}


@pytest.mark.parametrize("name,units", [
    ("normal", [(1.0, 5.0, 3.9, (), 0.0)]),
    ("fast_speaker_needs_speedup", [(1.0, 5.0, 4.8, (), 0.0)]),
    ("pauses_absorb_overrun", [(1.0, 5.0, 4.5, (0.5,), 0.0)]),
    ("slow_speaker_pauses_and_slowdown", [(1.0, 5.0, 2.9, (0.3,), 1.0)]),
    ("short_utterance", [(1.0, 1.5, 0.8, (), 0.0), (4.0, 6.0, 1.9, (), 0.0)]),
    ("contiguous_units", [(1.0, 3.0, 1.9, (), 0.0), (3.0, 5.0, 1.9, (), 0.0)]),
    ("many_units", [(0.5 + 3 * k, 2.8 + 3 * k, 2.2 + 0.05 * k, (), 0.0) for k in range(6)]),
])
def test_feasible_cases_are_in_sync(tmp_path, name, units):
    recs, summary, rep = run_case(tmp_path, units, 20.0)
    e = errs(rep)
    assert len(e) == len(units), name
    for uid, (se, ee) in e.items():
        assert abs(se) < 0.05, f"{name}: unit {uid} start error {se}"
        # rule: a dub may end up to END_SLACK after the speaker; +0.06 s for stretch/measurement error
        assert -0.3 < ee <= END_SLACK + 0.06, f"{name}: unit {uid} end error {ee}"
    assert all(0.85 <= r["speed"] <= 1.2 for r in recs)
    assert summary["sync_miss"] == 0


def test_pauses_are_used_before_speed(tmp_path):
    recs, _, _ = run_case(tmp_path, [(1.0, 5.0, 4.5, (0.5,), 0.0)], 20.0)
    assert recs[0]["speed"] == 1.0 and recs[0]["pause_change"] < 0


def test_short_dub_is_slowed_within_limit(tmp_path):
    recs, _, _ = run_case(tmp_path, [(1.0, 5.0, 2.9, (0.3,), 1.0)], 20.0)
    assert recs[0]["pause_change"] > 0 and 0.85 <= recs[0]["speed"] < 1.0


def test_too_long_is_condensed_then_fits(tmp_path):
    recs, summary, rep = run_case(tmp_path, [(1.0, 5.0, 8.0, (), 0.0)], 20.0, condense_to=3.9)
    assert summary["condensed"] == 1
    se, ee = errs(rep)[1]
    assert abs(se) < 0.05 and abs(ee) < 0.3


def test_impossible_case_is_flagged_not_hidden(tmp_path):
    recs, summary, rep = run_case(tmp_path, [(1.0, 5.0, 8.0, (), 0.0), (5.5, 8.0, 2.3, (), 0.0)], 20.0)
    assert "overrun" in recs[0]["flags"] and "late_end" in recs[0]["flags"]
    assert recs[0]["speed"] == 1.2
    se, _ = errs(rep)[2]
    assert recs[1]["flags"] and se > 0.05          # the next unit had to wait, and that is reported


def test_unit_free_metrics_are_reported(tmp_path):
    _, _, rep = run_case(tmp_path, [(1.0, 5.0, 3.9, (), 0.0), (6.0, 9.0, 2.9, (), 0.0)], 20.0)
    uf = rep["unit_free"]
    assert uf["phrase_onsets"] == 2 and uf["phrase_onsets_matched_0.3s"] == 2
    assert uf["speech_overlap_iou"] > 0.8


def test_delay_is_absorbed_by_the_next_clip(tmp_path):
    # unit 1 is impossible (overrun), unit 2 is pushed late and must catch up so unit 3 is NOT delayed
    units = [(1.0, 5.0, 8.0, (), 0.0), (7.0, 9.5, 2.4, (), 0.0), (10.2, 12.0, 1.7, (), 0.0)]
    recs, summary, rep = run_case(tmp_path, units, 20.0)
    by = {r["id"]: r for r in recs}
    assert "overrun" in by[1]["flags"]
    assert by[2]["delay"] > 0.05 and by[2]["fit"].get("caught_up") and by[2]["speed"] > 1.0
    assert by[3]["delay"] == 0.0 and abs(errs(rep)[3][0]) < 0.05
