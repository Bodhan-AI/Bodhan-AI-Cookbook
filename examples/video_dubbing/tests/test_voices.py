"""Speaker voices: one voice unless pitches form two clearly separate groups."""

import pytest
from bodhan_dub import voices

UNITS = [{"start": i, "end": i + 1} for i in range(8)]


def genders_for(pitches, monkeypatch):
    it = iter(pitches)
    monkeypatch.setattr(voices.audio, "median_pitch", lambda wav, regions: next(it))
    return voices.speaker_genders(UNITS[: len(pitches)], wav=None)


def test_one_noisy_female_narrator_stays_one_voice(monkeypatch):
    genders, info = genders_for([250, 219, 162, 256, 199, 164, 262, 230], monkeypatch)
    assert info["speakers"] == 1 and set(genders) == {"female"}


def test_excited_male_speaker_stays_male(monkeypatch):
    genders, info = genders_for([138, 129, 198, 158], monkeypatch)
    assert info["speakers"] == 1 and set(genders) == {"male"}


def test_male_and_female_conversation_gets_two_voices(monkeypatch):
    genders, info = genders_for([118, 125, 212, 205, 121, 220, 0, 115], monkeypatch)
    assert info["speakers"] == 2
    assert genders == ["male", "male", "female", "female", "male", "female", "female", "male"]


def test_voice_overrides():
    assert voices.voice_for("hi", "female", {}) == "Kavya"
    assert voices.voice_for("hi", "female", {"hi": "male"}) == "Amit"
    assert voices.voice_for("hi", "male", {"hi": "Suhani"}) == "Suhani"
    assert voices.voice_for("bn", "male", {"bn": {"female": "Ishita"}}) == "Sourav"


def test_unknown_voice_is_rejected(tmp_path):
    path = tmp_path / "voices.yaml"
    path.write_text("hi: Nobody\n")
    with pytest.raises(ValueError):
        voices.load_overrides(path)
