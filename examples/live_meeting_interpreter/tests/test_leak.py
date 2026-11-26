import numpy as np

from app.audio.leak import LEAK_GAIN, check_sound, leak_gain

SR = 16000


def test_check_sound_is_a_short_speech_clip():
    clip = check_sound()
    assert abs(len(clip) / SR - 1.2) < 0.01 and 0.1 < np.abs(clip).max() <= 0.31


def test_clip_found_at_its_level_and_offset_inside_meeting_audio():
    clip = check_sound()
    rng = np.random.default_rng(7)
    meeting = (0.05 * rng.standard_normal(3 * SR)).astype(np.float32)
    leaked = meeting.copy()
    leaked[int(0.7 * SR):int(0.7 * SR) + len(clip)] += 0.85 * clip  # what the ASUS output did
    assert abs(leak_gain(leaked, clip) - 0.85) < 0.05
    assert leak_gain(meeting, clip) < LEAK_GAIN / 4                    # meeting audio alone: no alarm


def test_other_speech_does_not_count():
    clip = check_sound()
    other = np.concatenate([np.zeros(SR // 2, np.float32), clip[::-1], clip[::-1]])  # same voice, not the clip
    assert leak_gain(other, clip) < LEAK_GAIN


def test_device_checks_are_remembered(tmp_path):
    from app.services import checked_gain, remember_check
    path = tmp_path / "checks.json"
    assert checked_gain("Headphones", path) is None
    remember_check("Headphones (USB Audio)", 0.0012, path)
    remember_check("AI Noise-cancelling Output (ASUS Utility)", 0.955, path)
    assert checked_gain("Headphones (USB Audio)", path) == 0.001
    assert checked_gain("AI Noise-cancelling Output (ASUS Utility)", path) == 0.955
    path.write_text("not json", encoding="utf-8")
    assert checked_gain("Headphones (USB Audio)", path) is None  # a broken file means: test again


def test_silence_and_short_audio():
    clip = check_sound()
    assert leak_gain(np.zeros(2 * SR, np.float32), clip) == 0.0
    assert leak_gain(clip[:100], clip) == 0.0
