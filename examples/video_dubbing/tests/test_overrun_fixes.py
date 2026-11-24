"""Fixes for overruns and their side effects (2026-11-20):
  * condense: LLM errors are not NO answers, results are cached, a third candidate aims at a milder target;
  * catch-up condense: a delayed clip that max speed cannot save is shortened for the time it really has;
  * subtitles never run past the end of the video (length target)."""

import subprocess

import numpy as np
import soundfile as sf

from bodhan_dub import condense as cmod
from bodhan_dub.assemble import assemble
from bodhan_dub.condense import condense
from bodhan_dub.dub_language import Deps, DubConfig, dub_language
from bodhan_dub.subtitles import clip_cues

from test_sync_synthetic import fake_clip

OLD = "So it is actually the situation that a primitive cell faced a very long time ago in the sea."


def llm_script(replies):
    """Fake LLM answering from a list; judge calls and shorten calls share it, in order. Exceptions are raised."""
    calls = []

    def llm(msgs):
        calls.append(msgs)
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    return llm, calls


# ---------------------------------------------------------------- condense
def test_judge_error_is_not_a_no_and_is_not_cached(tmp_path):
    cmod.reset_stats()
    llm, calls = llm_script(["It is the situation a primitive cell faced long ago.", RuntimeError("429")])
    assert condense(OLD, 50, llm=llm, cache_dir=tmp_path) is None
    assert cmod.STATS["llm_errors"] == 1 and cmod.STATS["rejected"] == 0
    assert not list(tmp_path.glob("condense_*.json"))          # an error is never cached
    llm2, _ = llm_script(["It is the situation a primitive cell faced long ago.", "YES"])
    assert condense(OLD, 50, llm=llm2, cache_dir=tmp_path) == "It is the situation a primitive cell faced long ago."


def test_result_is_cached_so_a_rerun_gives_the_same_text(tmp_path):
    llm, calls = llm_script(["It is the situation a primitive cell faced long ago.", "YES"])
    first = condense(OLD, 50, "prev", "next", llm=llm, cache_dir=tmp_path)
    n = len(calls)
    llm2, calls2 = llm_script([])                               # would fail if called
    assert condense(OLD, 50, "prev", "next", llm=llm2, cache_dir=tmp_path) == first and not calls2 and n == 2


def test_a_rejection_is_cached_too(tmp_path):
    llm, _ = llm_script(["Cell.", "Cell.", "Cell."])            # 3 rule rejections ("too short")
    assert condense(OLD, 50, llm=llm, cache_dir=tmp_path) is None
    llm2, calls2 = llm_script([])
    assert condense(OLD, 50, llm=llm2, cache_dir=tmp_path) is None and not calls2


def test_third_candidate_aims_at_a_milder_target_and_partial_gain_is_kept():
    cmod.reset_stats()
    partial = "So it is the situation that a primitive cell faced a long time ago in the sea."
    llm, calls = llm_script(["Cell faced sea.", "Old cell, sea.", partial, "YES"])
    assert condense(OLD, 30, llm=llm) == partial
    third = calls[2][1]["content"]
    assert f"at most {(len(OLD) + 30) // 2} characters" in third and "fine to miss the target" in third
    assert cmod.STATS["accepted"] == 1


def test_judge_no_is_still_a_rejection():
    llm, _ = llm_script(["It is the situation a primitive cell faced long ago.", "NO"] * 3)
    assert condense(OLD, 50, llm=llm) is None


# ---------------------------------------------------------------- catch-up condense (synthetic, real fit/placement)
def _run(tmp_path, catchup: bool):
    """Unit 1 cannot be shortened and overruns; unit 2 is pushed late and is too long for the time it has left,
    even at 1.2x; unit 3 follows right after unit 2."""
    plan = {}
    units = [(1.0, 5.0, 8.0), (7.0, 9.5, 2.9), (10.0, 12.0, 1.9)]
    segs = []
    for i, (s, e, voiced) in enumerate(units, 1):
        text = f"seg{i}"
        plan[text] = voiced
        segs.append({"id": i, "start": s, "end": e, "translation": text, "src": f"English sentence number {i} " * 3,
                     "inner_pause": 0.0})

    def tts(text, variant):
        p = tmp_path / f"tts_{text}_{variant}.wav"
        fake_clip(p, plan[text])
        return {"ok": True, "path": str(p), "dur": plan[text], "chars": len(text), "cached": False}

    def condense(en, target, prev, nxt):
        return None if "number 1" in en else en[:target]       # unit 1 can never be shortened

    def translate(en):
        t = f"short{len(en)}"
        plan[t] = 1.5
        return t

    deps = Deps(tts=tts, qa=lambda p, r: None, translate=translate, condense=condense)
    cfg = DubConfig(qa=False, vad_backend="energy", sync_rounds=0, catchup_condense=catchup)
    recs, summary = dub_language(segs, 20.0, tmp_path, cfg, deps)
    return {r["id"]: r for r in recs}, summary


def test_without_catch_up_condense_the_delay_ripples_on(tmp_path):
    by, summary = _run(tmp_path, catchup=False)
    assert "overrun" in by[1]["flags"] and by[2]["speed"] == 1.2 and by[3]["delay"] > 0.15


def test_catch_up_condense_stops_the_delay_at_the_delayed_clip(tmp_path):
    by, summary = _run(tmp_path, catchup=True)
    assert "overrun" in by[1]["flags"]                          # the root cause is still reported
    assert by[2]["delay"] > 0.15 and "recondensed" in by[2] and by[2]["text"].startswith("short")
    assert by[2]["voiced_end_at"] <= 9.75 + 0.05                # ends within its own limit now
    assert by[3]["delay"] == 0.0
    assert summary["recondensed_on_catch_up"] == 1


# ---------------------------------------------------------------- subtitles stay inside the video
def test_clip_cues():
    cues = [(1.0, 2.0, "a"), (2.5, 3.6, "b"), (2.9, 3.5, "c")]
    assert clip_cues(cues, 3.0) == [(1.0, 2.0, "a"), (2.5, 3.0, "b")]


def test_mp4_is_not_longer_than_the_video_when_the_last_dub_runs_past_the_end(tmp_path):
    video = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:d=3",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(video)], check=True)
    clip = tmp_path / "clip.wav"
    sf.write(clip, (np.random.default_rng(0).standard_normal(int(1.6 * 24000)) * 0.2).astype("float32"), 24000)
    recs = [{"id": 1, "clip_path": str(clip), "placed_start": 2.0, "placed_end": 3.6, "onset": 2.0,
             "voiced_end_at": 3.6, "text": "आखिरी वाक्य"}]
    rep = assemble(recs, [{"id": 1, "start": 2.0, "end": 2.9}], video, tmp_path / "out.mp4", tmp_path / "asm")
    assert rep["length_error_ms"] <= 5
    srt = (tmp_path / "asm" / "subtitles.srt").read_text(encoding="utf-8")
    assert "00:00:03,000" in srt and "00:00:03,6" not in srt


def test_cache_is_per_model(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "model-a")
    llm, _ = llm_script(["It is the situation a primitive cell faced long ago.", "YES"])
    condense(OLD, 50, llm=llm, cache_dir=tmp_path)
    monkeypatch.setenv("LLM_MODEL", "model-b")
    llm2, calls2 = llm_script(["It is the situation a primitive cell faced long ago.", "YES"])
    condense(OLD, 50, llm=llm2, cache_dir=tmp_path)
    assert len(calls2) == 2 and len(list(tmp_path.glob("condense_*.json"))) == 2
