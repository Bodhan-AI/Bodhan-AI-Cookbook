

def test_peaky_speech_still_reaches_minus_16_lufs_without_clipping(tmp_path):
    import numpy as np

    from bodhan_dub import audio
    rng = np.random.default_rng(1)
    sr = audio.SR
    t = np.arange(sr * 12) / sr
    env = (np.sin(2 * np.pi * 1.7 * t) > -0.3).astype("float32")                      # speech-like on/off envelope
    x = (0.02 * rng.standard_normal(len(t)).astype("float32") * env)
    for k in range(0, len(x) - 200, sr // 3):                                          # sparse sharp peaks: the TTS crest factor problem
        x[k:k + 60] += 0.5 * np.hanning(60).astype("float32")
    wav = tmp_path / "d.wav"
    import soundfile as sf
    sf.write(str(wav), x, sr)
    before = audio.measure_lufs(wav)
    y, after, gain = audio.normalise_speech(x, before, wav)
    assert before is not None and after is not None
    assert abs(after - (-16.0)) <= 1.0, (before, after, gain)                          # a plain gain + peak scaling would land near -21
    assert float(np.abs(y).max()) <= 0.9
