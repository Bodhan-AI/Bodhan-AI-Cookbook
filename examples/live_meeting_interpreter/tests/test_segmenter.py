import numpy as np

from app.audio.vad import FRAME, Segmenter, make_segmenter, replay
from app.config import VadSettings


def fake_vad(frame):  # loud frames are "speech"
    return 1.0 if float(np.abs(frame).mean()) > 0.1 else 0.0


def speech(ms):
    return np.full(int(16 * ms), 0.5, dtype=np.float32)


def silence(ms):
    return np.zeros(int(16 * ms), dtype=np.float32)


def make(**kw):
    defaults = dict(end_silence_ms=600, soft_max_s=8.0, hard_max_s=25.0, min_speech_ms=300)
    defaults.update(kw)
    return Segmenter(fake_vad, **defaults)


def test_utterance_ends_after_pause():
    seg = make()
    assert seg.push(silence(500)) == []
    assert seg.push(speech(1500)) == []
    out = seg.push(silence(800))
    assert len(out) == 1
    # ~1.5 s speech + pre-roll + ~200 ms kept tail, trailing silence trimmed
    assert 1.5 <= out[0].duration_s <= 2.2


def test_short_noise_is_dropped():
    seg = make()
    assert seg.push(np.concatenate([speech(100), silence(1000)])) == []


def test_hard_max_forces_cut_below_30s():
    seg = make(hard_max_s=5.0, soft_max_s=4.0)
    out = seg.push(speech(12000))
    assert len(out) >= 2
    assert all(s.duration_s <= 5.0 for s in out)


def test_soft_max_cuts_on_short_pause():
    seg = make(soft_max_s=2.0, end_silence_ms=600)
    audio = np.concatenate([speech(2500), silence(250), speech(1000), silence(800)])
    out = seg.push(audio)
    assert len(out) == 2  # 250 ms pause was enough after 2 s of speech


def test_flush_closes_open_utterance():
    seg = make()
    seg.push(speech(1000))
    assert seg.in_speech
    out = seg.flush()
    assert len(out) == 1 and not seg.in_speech


def test_current_shows_the_utterance_in_progress():
    seg = make()
    assert seg.current() is None
    seg.push(speech(2000))
    live = seg.current()
    assert live.seq == 1 and 1.9 < live.duration_s < 2.4  # speech so far, incl. pre-roll
    done = seg.push(silence(800))
    assert done[0].seq == live.seq and seg.current() is None


def test_partial_frames_are_buffered():
    seg = make()
    for _ in range(100):
        seg.push(speech(1000)[: FRAME // 3])
    assert seg.in_speech


def test_replay_timestamps_use_recording_time():
    audio = np.concatenate([silence(2000), speech(1500), silence(1000), speech(800)])
    out = replay(audio, lambda clock: make(clock=clock))
    assert len(out) == 2
    first, last = out
    # first utterance starts ~2.0 s in and is sent ~600 ms into the following pause
    assert 1.9 <= first.started_at <= 2.2
    assert 3.9 <= first.ended_at <= 4.3
    # ...but the speech itself stopped at 3.5 s (what the session record's timings use)
    assert 3.4 <= first.speech_end <= 3.6
    # the second is still open when the recording ends, so it is flushed at the end
    assert last.ended_at == len(audio) / 16000


def test_make_segmenter_uses_settings():
    v = VadSettings(threshold=0.5, end_silence_ms=300, soft_max_s=8.0, hard_max_s=25.0)
    seg = make_segmenter(v, vad=fake_vad)
    seg.push(speech(1000))
    assert len(seg.push(silence(400))) == 1  # 300 ms pause is enough with these settings
