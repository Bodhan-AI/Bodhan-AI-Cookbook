from pathlib import Path

import pytest

from bodhan_shared.errors import TaskError
from bodhan_shared.media import audio_chunks, format_srt_time, make_srt, split_speech
from tests.conftest import fake_wav


def test_sentence_splitting_preserves_all_words():
    text = "This is one sentence. This is another longer sentence! Final short line."
    parts = split_speech(text, max_chars=23)
    assert all(len(x) <= 23 for x in parts)
    assert " ".join(" ".join(parts).split()) == text


def test_srt_timestamp_and_invalid():
    assert format_srt_time(3661.125) == "01:01:01,125"
    srt = make_srt([{"start": 1, "end": 2.5, "text": "Hello"}])
    assert "00:00:01,000 --> 00:00:02,500" in srt
    with pytest.raises(TaskError):
        make_srt([{"start": 10, "end": 5, "text": "bad"}])


def test_audio_chunking_never_exceeds_25s(tmp_path):
    wav = tmp_path / "sample.wav"
    wav.write_bytes(fake_wav(seconds=52))
    chunks = audio_chunks(wav, tmp_path / "chunks", seconds=25)
    assert len(chunks) == 3
    assert all(0 < x["seconds"] <= 25 for x in chunks)
    assert abs(chunks[-1]["end"] - 52) < .1
