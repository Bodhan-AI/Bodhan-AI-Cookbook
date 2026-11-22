import numpy as np
import soundfile as sf

from bodhan_dub.pausefit import PAUSE_FLOOR, adjust_pauses, analyze, analyze_file, render_clip

SR = 24000


def clip(lead=0.1, parts=(1.0, 1.0), pauses=(0.4,), tail=0.1):
    rng = np.random.default_rng(0)
    w = [np.zeros(int(SR * lead), "float32")]
    for i, p in enumerate(parts):
        w.append((rng.standard_normal(int(SR * p)) * 0.2).astype("float32"))
        if i < len(pauses):
            w.append(np.zeros(int(SR * pauses[i]), "float32"))
    w.append(np.zeros(int(SR * tail), "float32"))
    return np.concatenate(w)


def test_analyze_finds_lead_tail_and_pauses():
    info = analyze(clip(), SR)
    assert abs(info["lead"] - 0.1) < 0.011 and abs(info["voiced"] - 2.4) < 0.02
    assert info["n_pauses"] == 1 and abs(info["max_shrink"] - (0.4 - PAUSE_FLOOR)) < 0.02


def test_adjust_pauses_shrinks_and_widens():
    w = clip()
    info = analyze(w, SR)
    shorter, ch = adjust_pauses(w, SR, info["pauses"], -0.2)
    assert abs(ch + 0.2) < 1e-3 and abs((len(w) - len(shorter)) / SR - 0.2) < 0.002
    longer, ch2 = adjust_pauses(w, SR, info["pauses"], 0.3)
    assert abs(ch2 - 0.3) < 1e-3 and abs((len(longer) - len(w)) / SR - 0.3) < 0.002
    _, ch3 = adjust_pauses(w, SR, info["pauses"], -5.0)          # cannot go below the floor
    assert abs(ch3 + (0.4 - PAUSE_FLOOR)) < 0.02


def test_render_trims_edges_adjusts_and_stretches(tmp_path):
    sf.write(tmp_path / "a.wav", clip(lead=0.2, tail=0.3), SR)
    out = render_clip(tmp_path / "a.wav", tmp_path / "b.wav", pause_delta=-0.2, speed=1.1)
    assert out["lead"] <= 0.04                                    # edge silence trimmed to ~30 ms
    assert abs(out["voiced"] - (2.4 - 0.2) / 1.1) < 0.05
    assert abs(analyze_file(tmp_path / "b.wav")["voiced"] - out["voiced"]) < 1e-6


def test_render_without_pauses_or_speed_is_just_trimmed(tmp_path):
    sf.write(tmp_path / "a.wav", clip(pauses=(), parts=(1.5,)), SR)
    out = render_clip(tmp_path / "a.wav", tmp_path / "b.wav", 0.0, 1.0)
    assert abs(out["voiced"] - 1.5) < 0.02 and out["pause_change"] == 0.0
