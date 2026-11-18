"""speak_text over the fake backend (WAV duration = chars / 13.5 s)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bodhan_mcp.core import catalog, media
from bodhan_mcp.core.errors import BodhanError
from bodhan_mcp.tools.speak import speak_text


def write(tmp_path, text, name="say.txt"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def sentences(n: int) -> str:
    return " ".join(f"Sentence number {i:03d} is spoken here." for i in range(n))


def test_long_text_is_clipped_and_joined_to_the_predicted_length(bodhan, tmp_path):
    text = sentences(90)
    assert len(text) > 3000
    res = speak_text(write(tmp_path, text), "ta", out_dir=str(tmp_path / "o"))
    reqs = bodhan.of("tts")
    assert len(reqs) == res.clips > 1
    assert max(r.input_len for r in reqs) <= 400
    clips_s = sum(r.input_len for r in reqs) / 13.5
    expected = clips_s + 0.12 * (len(reqs) - 1)
    assert abs(res.duration_s - expected) <= 0.1 * expected
    assert res.warnings == [] and res.voice == "Anitha"
    assert bodhan.of("asr") == []  # synthesis never verifies by transcribing


def test_mp3_output_is_a_real_mp3(bodhan, tmp_path):
    res = speak_text(write(tmp_path, sentences(6)), "hi", format="mp3", out_dir=str(tmp_path / "o"))
    assert res.audio_path.endswith(".mp3")
    assert media.probe_audio(res.audio_path)["codec"] == "mp3"


def test_an_mp3_response_is_accepted_and_decoded(bodhan, tmp_path):
    bodhan.tts_mp3 = True
    res = speak_text(write(tmp_path, sentences(6)), "hi", out_dir=str(tmp_path / "o"))
    assert Path(res.audio_path).read_bytes()[:4] == b"RIFF"
    assert res.duration_s == pytest.approx(len(sentences(6)) / 13.5, rel=0.1)


def test_a_clip_far_off_the_prediction_is_resynthesised_in_smaller_pieces(bodhan, tmp_path):
    text = " ".join(f"Sentence number {i:02d} is here." for i in range(13))
    assert 340 < len(text) <= 400
    bodhan.tts_factors = [0.3]
    res = speak_text(write(tmp_path, text), "ta", out_dir=str(tmp_path / "o"))
    reqs = bodhan.of("tts")
    assert res.clips == 1 and len(reqs) > 2
    assert all(r.input_len <= 200 for r in reqs[1:])
    assert res.warnings == ["duration_off_target_clip_0"]
    assert res.duration_s == pytest.approx(len(text) / 13.5, rel=0.1)


def test_unknown_voice_lists_valid_names_and_sends_nothing(bodhan, tmp_path):
    with pytest.raises(BodhanError) as exc:
        speak_text(write(tmp_path, "Hello."), "ta", voice="Nobody")
    assert exc.value.code == "UNSUPPORTED_VOICE"
    assert "Anitha" in exc.value.hint and "Arun" in exc.value.hint
    assert bodhan.requests == []


def test_english_uses_amit(bodhan, tmp_path):
    res = speak_text(write(tmp_path, "Hello there."), "en", out_dir=str(tmp_path / "o"))
    assert res.voice == "Amit" and bodhan.of("tts")[0].json["voice"] == "Amit"


def test_gender_picks_the_female_tamil_voice(bodhan, tmp_path):
    res = speak_text(write(tmp_path, "Hello there."), "ta", gender="female", out_dir=str(tmp_path / "o"))
    assert res.voice == "Anitha"
    res = speak_text(write(tmp_path, "Hello there.", "b.txt"), "ta", gender="male", out_dir=str(tmp_path / "o"))
    assert res.voice == "Arun"


@pytest.mark.parametrize("lang", ["fr", "bho", "mni-Beng"])
def test_unsupported_language(bodhan, tmp_path, lang):
    with pytest.raises(BodhanError) as exc:
        speak_text(write(tmp_path, "Hello."), lang)
    assert exc.value.code == "UNSUPPORTED_LANGUAGE"
    assert all(c in exc.value.hint for c in catalog.TTS_LANGUAGES)
    assert bodhan.requests == []


def test_unknown_style(bodhan, tmp_path):
    with pytest.raises(BodhanError) as exc:
        speak_text(write(tmp_path, "Hello."), "ta", style="pirate")
    assert exc.value.code == "UNSUPPORTED_STYLE" and bodhan.requests == []


def test_instructions_is_a_json_string(bodhan, tmp_path):
    speak_text(write(tmp_path, "Hello."), "ta", style="news", out_dir=str(tmp_path / "o"))
    speak_text(write(tmp_path, "Hello again.", "b.txt"), "ta", out_dir=str(tmp_path / "o"))
    first, second = (r.json for r in bodhan.of("tts"))
    assert isinstance(first["instructions"], str)
    assert json.loads(first["instructions"]) == {"lang": "ta", "style": "news"}
    assert json.loads(second["instructions"]) == {"lang": "ta"}
    assert first["model"] == "indic-speak"


def test_second_identical_call_is_free(bodhan, tmp_path):
    p = write(tmp_path, sentences(20))
    first = speak_text(p, "ta", out_dir=str(tmp_path / "o"))
    n = len(bodhan.requests)
    second = speak_text(p, "ta", out_dir=str(tmp_path / "o"))
    assert len(bodhan.requests) == n and first.cost_inr > 0 and second.cost_inr == 0


def test_empty_file(bodhan, tmp_path):
    with pytest.raises(BodhanError) as exc:
        speak_text(write(tmp_path, "  \n"), "ta")
    assert exc.value.code == "EMPTY_INPUT" and bodhan.requests == []
