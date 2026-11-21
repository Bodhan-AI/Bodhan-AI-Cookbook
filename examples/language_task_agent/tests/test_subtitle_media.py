import wave

import pytest

from bodhan_core import media


def narrated_pattern(path):
    # Synthetic PCM signal/silence, not speech or real ASR evidence.
    tone = bytes.fromhex("401fc0e0") * 8000
    pcm = tone * 20 + b"\0\0" * 16000 + tone * 20 + b"\0\0" * 16000 + tone * 18
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(pcm)
    return pcm


def test_pause_chunks_use_quiet_boundaries_without_losing_samples(tmp_path):
    source = tmp_path / "source.wav"
    pcm = narrated_pattern(source)
    parts = media.audio_chunks(source, tmp_path, prefer_pauses=True)
    assert len(parts) == 3
    assert parts[0][2] == pytest.approx(20.5, abs=0.05)
    assert parts[1][2] == pytest.approx(41.5, abs=0.05)
    joined = b""
    for path, start, end in parts:
        assert 0 < end - start <= 25
        with wave.open(str(path)) as audio:
            joined += audio.readframes(audio.getnframes())
    assert joined == pcm
    assert parts[0][1] == 0 and parts[-1][2] == 60
    assert all(left[2] == right[1] for left, right in zip(parts, parts[1:]))


def test_fixed_and_pause_part_references_do_not_overwrite_each_other(tmp_path):
    source = tmp_path / "source.wav"
    narrated_pattern(source)
    fixed = media.audio_chunks(source, tmp_path)
    snapshots = {path: path.read_bytes() for path, _, _ in fixed}
    paused = media.audio_chunks(source, tmp_path, prefer_pauses=True)
    assert set(snapshots).isdisjoint(path for path, _, _ in paused)
    assert all(path.read_bytes() == data for path, data in snapshots.items())


def test_readable_cues_preserve_words_and_cover_the_source_window():
    text = ("The group records each observation carefully, with its date and measurement unit. " * 5).strip()
    cues = media.subtitle_cues(text, 5, 30)
    assert len(cues) >= 5
    assert " ".join(cue["text"].replace("\n", " ") for cue in cues) == text
    assert cues[0]["start"] == 5 and cues[-1]["end"] == 30
    for cue in cues:
        assert 0 < cue["end"] - cue["start"] <= 6
        assert len(cue["text"].splitlines()) <= 2
        assert all(len(line) <= 42 for line in cue["text"].splitlines())
    assert all(a["end"] == b["start"] for a, b in zip(cues, cues[1:]))


@pytest.mark.parametrize("text,seconds", [("x" * 90, 25), ("One two three.", 0.001)])
def test_caption_edge_cases_keep_text_and_positive_timing(text, seconds):
    cues = media.subtitle_cues(text, 0, seconds)
    assert " ".join(c["text"].replace("\n", " ") for c in cues) == text
    assert all(c["end"] > c["start"] for c in cues)
    assert cues[-1]["end"] == seconds


def test_pause_analysis_shares_one_conversion_deadline(tmp_path, monkeypatch):
    import shutil
    import time

    source = tmp_path / "source.wav"
    narrated_pattern(source)
    clock, observed = [0], []
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def convert(args, timeout):
        observed.append(timeout)
        clock[0] += 2
        if args[0] == "ffprobe":
            return b'{"format":{"duration":"60"}}'
        if "-af" not in args:
            shutil.copyfile(source, args[-1])
        return b""

    monkeypatch.setattr(media, "command", convert)
    media.audio_chunks(source, tmp_path, timeout=10, prefer_pauses=True)
    assert observed == [10, 8, 6]


def test_30_second_context_keeps_the_default_25_second_behavior(tmp_path):
    source = tmp_path / "source.wav"
    narrated_pattern(source)
    default = media.audio_chunks(source, tmp_path)
    extended = media.audio_chunks(source, tmp_path, window_seconds=30)
    assert [(start, end) for _, start, end in default] == [(0, 25), (25, 50), (50, 60)]
    assert [(start, end) for _, start, end in extended] == [(0, 30), (30, 60)]
    assert {p for p, _, _ in default}.isdisjoint(p for p, _, _ in extended)
