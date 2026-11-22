"""Robustness pieces added for unseen videos: one edge rule everywhere, the closed sync loop, ASR replies
that are cut off, voice/background separation (stitching + gate) and keeping the background under the dub."""

import subprocess

import numpy as np
import pytest
import soundfile as sf
import torch

from bodhan_dub import transcribe
from bodhan_dub.assemble import assemble, build_speech_track
from bodhan_dub.chunking import Chunk
from bodhan_dub.dub_language import Deps, DubConfig, dub_language
from bodhan_dub.separate import pick_windows, probe_background, separate_array
from bodhan_dub.sync_check import measure_sync
from bodhan_dub.vad import refine_edges

SR16 = 16000


# ---------------------------------------------------------------- one edge rule, any noise floor
def _voice_with_tail(floor_db, seed=0):
    """1.0-2.0 s voice, then a tail fading 60 dB over 0.3 s, on a noise floor of floor_db."""
    rng = np.random.default_rng(seed)
    w = rng.standard_normal(4 * SR16).astype("float32") * (10 ** (floor_db / 20))
    body = rng.standard_normal(SR16).astype("float32") * 0.3
    tail_n = int(0.3 * SR16)
    tail = rng.standard_normal(tail_n).astype("float32") * 0.3 * (10 ** (np.linspace(0, -60, tail_n) / 20))
    w[SR16 : 2 * SR16] += body
    w[2 * SR16 : 2 * SR16 + tail_n] += tail.astype("float32")
    return w


def test_same_voice_gets_same_edges_in_silence_and_in_noise():
    quiet = refine_edges(_voice_with_tail(-140), SR16, [(1.02, 2.25)])[0]
    noisy = refine_edges(_voice_with_tail(-55), SR16, [(1.02, 2.25)])[0]
    assert abs(quiet[0] - noisy[0]) < 0.02
    assert abs(quiet[1] - noisy[1]) < 0.03          # old floor-based rule: ~0.15 s apart
    assert abs(quiet[1] - 2.15) < 0.04              # edge = 30 dB below the speech level


# ---------------------------------------------------------------- closed loop
def _soft_onset_clip(path, soft=0.12, loud=3.0, sr=24000, seed=0):
    """A clip whose first `soft` s are 35 dB down: our clip analysis (-40 dB) counts it as voice,
    the source-side detector (30 dB rule) does not. So without the loop the heard start is late."""
    rng = np.random.default_rng(seed)
    parts = [np.zeros(int(0.07 * sr)),
             rng.standard_normal(int(soft * sr)) * 0.2 * 10 ** (-35 / 20),
             rng.standard_normal(int(loud * sr)) * 0.2,
             np.zeros(int(0.06 * sr))]
    sf.write(path, np.concatenate(parts).astype("float32"), sr)


def _run_loop_case(tmp_path, rounds):
    clip = tmp_path / "tts.wav"
    _soft_onset_clip(clip)
    deps = Deps(tts=lambda t, v: {"ok": True, "path": str(clip), "dur": 3.25, "chars": len(t), "cached": False},
                qa=lambda p, r: None, translate=lambda en: None, condense=lambda *a: None)
    segs = [{"id": 1, "start": 1.0, "end": 4.2, "translation": "x", "src": "words", "inner_pause": 0.0}]
    work = tmp_path / f"w{rounds}"
    recs, summary = dub_language(segs, 10.0, work, DubConfig(qa=False, vad_backend="energy", sync_rounds=rounds), deps)
    t24 = work / "t24.wav"
    sf.write(t24, build_speech_track(recs, 10.0), 24000)
    t16 = work / "t16.wav"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(t24), "-ar", "16000", str(t16)], check=True)
    rep = measure_sync(t16, recs, segs, work / "m", backend="energy")
    return recs, summary, rep["units"][0]["start_err"]


def test_closed_loop_moves_the_heard_start_onto_the_source_start(tmp_path):
    _, _, err_without = _run_loop_case(tmp_path, 0)
    recs, summary, err_with = _run_loop_case(tmp_path, 2)
    assert err_without > 0.08                        # the soft onset made the dub sound late
    assert abs(err_with) < 0.04                      # the loop heard it and moved the clip
    assert summary["sync_loop"]["units_shifted"] == 1
    assert abs(recs[0]["measured_start_err"]) < 0.04


# ---------------------------------------------------------------- ASR replies that are cut off
def test_looks_truncated():
    assert transcribe.looks_truncated("A" * 200)
    assert not transcribe.looks_truncated("A" * 150)
    assert not transcribe.looks_truncated("a" * 300)          # mixed/lower case goes much longer
    assert transcribe.looks_truncated("a" * 430)


def test_split_point_prefers_the_longest_pause_near_the_middle():
    regs = [(0.0, 4.0), (4.2, 9.0), (10.0, 15.0), (15.1, 20.0)]
    assert abs(transcribe.split_point(regs, 20.0) - 9.5) < 1e-6


def test_cut_off_reply_is_retranscribed_in_pieces(tmp_path, monkeypatch):
    wav = np.random.default_rng(0).standard_normal(20 * SR16).astype("float32") * 0.1
    p = tmp_path / "chunk_0001.wav"
    sf.write(p, wav, SR16)

    def fake(path, lang, key, cache_dir=None):
        dur = sf.info(str(path)).duration
        text = "WORD " * 42 if dur > 15 else ("FIRST " * 25 if "_p0" in path.name else "SECOND " * 25)
        return {"ok": True, "text": text.strip(), "sec": 0.1, "cached": False}

    monkeypatch.setattr(transcribe, "transcribe_file", fake)
    monkeypatch.setattr(transcribe, "get_key", lambda s: "k")
    c = Chunk(id=1, start=100.0, end=120.0, speech=19.0, path=str(p), vad_start=100.0, vad_end=120.0,
              regions=[[100.0, 109.0], [110.0, 120.0]])
    r = transcribe.transcribe_chunks([c], tmp_path / "w")[0]
    assert r["text"] == ("FIRST " * 25).strip() + " " + ("SECOND " * 25).strip() and r["asr_pieces"] == 2
    assert r["text_unsplit"].startswith("WORD")


def test_split_result_is_kept_only_if_it_recovers_words(tmp_path, monkeypatch):
    p = tmp_path / "chunk_0002.wav"
    sf.write(p, np.zeros(20 * SR16, "float32"), SR16)
    monkeypatch.setattr(transcribe, "transcribe_file", lambda path, *a, **k: {
        "ok": True, "sec": 0.1, "cached": False,
        "text": ("WORD " * 42).strip() if sf.info(str(path)).duration > 15 else "X"})
    monkeypatch.setattr(transcribe, "get_key", lambda s: "k")
    c = Chunk(id=2, start=0.0, end=20.0, speech=19.0, path=str(p), regions=[[0.0, 9.0], [10.0, 20.0]])
    r = transcribe.transcribe_chunks([c], tmp_path / "w2")[0]
    assert r["text"].startswith("WORD") and "asr_pieces" not in r


# ---------------------------------------------------------------- separation: block stitching and gate
class FakeModel:
    samplerate = 44100
    sources = ["drums", "bass", "other", "vocals"]


def _stems(gains):
    def apply(model, x):
        return torch.stack([g * x[0] for g in gains])[None]
    return apply


def test_separation_blocks_stitch_without_seams():
    mix = np.random.default_rng(1).standard_normal((int(3.3 * 44100), 2)).astype("float32") * 0.1
    voc, bg = separate_array(mix, FakeModel(), block=1.0, context=0.2, apply_fn=_stems([0.1, 0.2, 0.3, 0.4]))
    mu = float(mix.mean(axis=1).mean())
    assert np.allclose(voc, 0.4 * (mix - mu) + mu, atol=1e-5)
    assert np.allclose(bg, 0.6 * (mix - mu), atol=1e-5)


def test_separation_uses_all_cores_even_after_silero_import():
    import os

    import silero_vad  # noqa: F401  (sets torch threads to 1 at import time)

    torch.set_num_threads(1)
    seen = []

    def apply(model, x):
        seen.append(torch.get_num_threads())
        return torch.stack([0.25 * x[0]] * 4)[None]

    separate_array(np.zeros((44100, 2), "float32"), FakeModel(), apply_fn=apply)
    assert seen and seen[0] == max(1, os.cpu_count() or 1)
    assert torch.get_num_threads() == 1                       # restored for the caller


def test_probe_windows_sit_on_speech():
    regs = [(0.0, 2.0), (10.0, 20.0), (50.0, 60.0)]
    wins = pick_windows(regs, 100.0)
    assert wins and all(any(b > a0 and a < b0 for a0, b0 in regs) for a, b in wins)
    assert all(0.0 <= a < b <= 100.0 for a, b in wins)


@pytest.mark.parametrize("gains,needed", [([0.2, 0.2, 0.2, 0.4], True),          # music bed: separate
                                          ([1e-3, 1e-3, 1e-3, 1.0], False)])     # clean voice: skip
def test_gate_separates_only_when_there_is_background(tmp_path, gains, needed):
    p = tmp_path / "mix.wav"
    sf.write(p, np.random.default_rng(2).standard_normal((20 * 44100, 2)).astype("float32") * 0.1, 44100)
    info = probe_background(p, [(1.0, 19.0)], model=FakeModel(), apply_fn=_stems(gains))
    assert info["needed"] is needed


# ---------------------------------------------------------------- background kept under the dub
def _tone(f, secs, sr, amp):
    t = np.arange(int(secs * sr)) / sr
    return (amp * np.sin(2 * np.pi * f * t)).astype("float32")


def _band_level(w, sr, f):
    spec = np.abs(np.fft.rfft(w * np.hanning(len(w))))
    freqs = np.fft.rfftfreq(len(w), 1 / sr)
    return float(spec[(freqs > f - 20) & (freqs < f + 20)].max())


def test_background_plays_under_the_dub_original_voice_does_not(tmp_path):
    video = tmp_path / "v.mkv"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=25:d=3",
                    "-f", "lavfi", "-i", "sine=frequency=800:sample_rate=44100:duration=3",
                    "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-c:a", "pcm_s16le", str(video)], check=True)
    bgp = tmp_path / "bg.wav"
    sf.write(bgp, np.column_stack([_tone(300, 3, 44100, 0.05)] * 2), 44100)
    clip = tmp_path / "clip.wav"
    sf.write(clip, _tone(2000, 1.0, 24000, 0.3), 24000)
    recs = [{"id": 1, "clip_path": str(clip), "placed_start": 1.0, "placed_end": 2.0, "onset": 1.0,
             "voiced_end_at": 2.0, "text": "x"}]
    rep = assemble(recs, [{"id": 1, "start": 1.0, "end": 2.0}], video, tmp_path / "o.mp4", tmp_path / "a",
                   background=bgp)
    mix, sr = sf.read(rep["final_audio"], dtype="float32")
    mono = mix.mean(axis=1)
    under, outside = mono[int(1.2 * sr) : int(1.8 * sr)], mono[int(0.2 * sr) : int(0.8 * sr)]
    assert _band_level(under, sr, 300) > 10 * _band_level(under, sr, 800)      # background, not original
    assert _band_level(outside, sr, 800) > 10 * _band_level(outside, sr, 300)  # original untouched
    assert rep["background_under_dub"] == "separated"
