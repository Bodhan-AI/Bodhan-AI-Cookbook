"""transcribe_audio over the fake backend."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bodhan_mcp.core import costs
from bodhan_mcp.core.errors import BodhanError
from bodhan_mcp.tools.transcribe import transcribe_audio


def test_long_audio_is_windowed_merged_costed_and_cached(bodhan, make_audio, tmp_path):
    audio = make_audio("long.wav", 61, gap_at=25.0)
    res = transcribe_audio(str(audio), out_dir=str(tmp_path / "o"))

    sent = bodhan.of("asr")
    assert len(sent) >= 3
    assert all(r.wav_seconds <= 30.0 for r in sent)
    starts = [c.start_s for c in res.chunks]
    assert starts == sorted(starts) and [c.index for c in res.chunks] == list(range(len(res.chunks)))
    assert res.chunks[0].cut_at == "pause" and abs(res.chunks[0].end_s - 25.0) < 0.5
    text = Path(res.text_path).read_text(encoding="utf-8")
    assert text == " ".join(f"transcribed window {i}" for i in range(len(sent)))
    assert res.cost_inr == costs.asr_cost(res.duration_s)
    assert res.cache_hits == 0

    again = transcribe_audio(str(audio), out_dir=str(tmp_path / "o"))
    assert len(bodhan.of("asr")) == len(sent)
    assert again.cache_hits == len(again.chunks) == len(res.chunks)
    assert again.cost_inr == 0
    assert Path(again.text_path).read_text(encoding="utf-8") == text


def test_m4a_is_converted_before_upload(bodhan, make_audio, tmp_path):
    wav = make_audio("in.wav", 4)
    m4a = tmp_path / "in.m4a"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-c:a", "aac", str(m4a)], check=True)
    transcribe_audio(str(m4a), out_dir=str(tmp_path / "o"))
    (req,) = bodhan.of("asr")
    assert req.filename.endswith(".wav") and req.mime == "audio/wav"
    assert req.file_bytes[:4] == b"RIFF"


def test_failing_middle_window_is_a_partial_failure(bodhan, make_audio, tmp_path):
    audio = make_audio("long.wav", 61, gap_at=25.0)
    bodhan.script("asr", "first part", bodhan.error(400), "last part")
    res = transcribe_audio(str(audio), out_dir=str(tmp_path / "o"))
    assert any("partial_failure" in w for w in res.warnings)
    assert Path(res.text_path).read_text(encoding="utf-8") == "first part last part"


def test_every_window_failing_is_no_speech(bodhan, make_audio, tmp_path):
    audio = make_audio("long.wav", 61, gap_at=25.0)
    bodhan.script("asr", *[bodhan.error(400) for _ in range(3)])
    with pytest.raises(BodhanError) as exc:
        transcribe_audio(str(audio), out_dir=str(tmp_path / "o"))
    assert exc.value.code == "NO_SPEECH"


def test_too_short_audio(bodhan, make_audio, tmp_path):
    audio = make_audio("tiny.wav", 0.2)
    with pytest.raises(BodhanError) as exc:
        transcribe_audio(str(audio), out_dir=str(tmp_path / "o"))
    assert exc.value.code == "AUDIO_TOO_SHORT"
    assert bodhan.requests == []


def test_missing_file(bodhan, tmp_path):
    with pytest.raises(BodhanError) as exc:
        transcribe_audio(str(tmp_path / "nope.wav"))
    assert exc.value.code == "MISSING_FILE"


def test_tiny_budget_refuses_before_any_request(bodhan, make_audio, tmp_path):
    audio = make_audio("long.wav", 61)
    with pytest.raises(BodhanError) as exc:
        transcribe_audio(str(audio), out_dir=str(tmp_path / "o"), max_cost_inr=0.01)
    assert exc.value.code == "BUDGET_EXCEEDED"
    assert bodhan.requests == []


def test_nested_json_text_is_unwrapped(bodhan, make_audio, tmp_path):
    audio = make_audio("a.wav", 3)
    bodhan.script("asr", '{"text":"x"}')
    res = transcribe_audio(str(audio), out_dir=str(tmp_path / "o"))
    assert Path(res.text_path).read_text(encoding="utf-8") == "x"
