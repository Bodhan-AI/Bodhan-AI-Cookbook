import io
import wave

import pytest
from narrator.audio_utils import join_wavs

from tests.helpers import duration, make_tone


def test_join_adds_pauses_between_clips():
    joined = join_wavs([make_tone(1), make_tone(1)], pause_ms=500)
    assert round(duration(joined), 2) == 2.5


def test_join_single_clip_has_no_pause():
    assert round(duration(join_wavs([make_tone(1)])), 2) == 1.0


def test_join_accepts_a_pause_for_each_gap():
    joined = join_wavs([make_tone(1), make_tone(1), make_tone(1)], pause_ms=[300, 700])
    assert round(duration(joined), 2) == 4.0


def test_join_rejects_the_wrong_number_of_pauses():
    with pytest.raises(ValueError):
        join_wavs([make_tone(1), make_tone(1)], pause_ms=[300, 700])


def test_join_keeps_the_audio_format():
    joined = join_wavs([make_tone(0.5), make_tone(0.5)])
    with wave.open(io.BytesIO(joined), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)


def test_join_rejects_mixed_formats():
    with pytest.raises(ValueError):
        join_wavs([make_tone(0.5, rate=24000), make_tone(0.5, rate=16000)])


def test_join_rejects_empty_list():
    with pytest.raises(ValueError):
        join_wavs([])