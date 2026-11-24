import pytest
from narrator import config
from narrator.errors import MissingKeyError


@pytest.fixture(autouse=True)
def clean_keys(monkeypatch):
    for name in ("BODHAN_API_KEY", "BODHAN_TRANSLATE_API_KEY", "BODHAN_SPEAK_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def test_missing_key_is_reported():
    with pytest.raises(MissingKeyError):
        config.get_api_key("translate")


def test_placeholder_key_counts_as_missing(monkeypatch):
    monkeypatch.setenv("BODHAN_API_KEY", "paste_your_real_key_here")
    with pytest.raises(MissingKeyError):
        config.get_api_key("speak")


def test_shared_key_is_used_for_both(monkeypatch):
    monkeypatch.setenv("BODHAN_API_KEY", "shared")
    assert config.get_api_key("translate") == "shared"
    assert config.get_api_key("speak") == "shared"


def test_separate_keys_win_over_the_shared_one(monkeypatch):
    monkeypatch.setenv("BODHAN_API_KEY", "shared")
    monkeypatch.setenv("BODHAN_TRANSLATE_API_KEY", "t-key")
    monkeypatch.setenv("BODHAN_SPEAK_API_KEY", "s-key")
    assert config.get_api_key("translate") == "t-key"
    assert config.get_api_key("speak") == "s-key"


def test_every_language_has_a_default_voice_from_the_list():
    for code in config.LANGUAGES:
        assert config.DEFAULT_VOICE[code] in config.VOICES[code]