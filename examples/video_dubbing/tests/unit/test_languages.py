"""
Unit tests for languages registry.
"""

from pathlib import Path

import pytest

from bodhan_dub.languages import LanguageRegistry


@pytest.fixture
def registry():
    yaml_path = Path(__file__).resolve().parent.parent.parent / "languages.yaml"
    return LanguageRegistry(yaml_path)


def test_registry_loads_production_languages(registry):
    codes = registry.get_supported_target_codes()
    assert "hi" in codes
    assert "ta" in codes
    assert "bn" in codes
    assert "te" in codes
    assert "en" not in codes  # 'en' is source, not a valid target


def test_registry_validates_target_langs(registry):
    valid = registry.validate_target_langs(["hi", "ta", "bn", "hi"])
    # Deduplicates and keeps order
    assert valid == ["hi", "ta", "bn"]


def test_registry_rejects_english_target(registry):
    with pytest.raises(ValueError, match="Target language cannot be English"):
        registry.validate_target_langs(["hi", "en"])


def test_registry_rejects_unknown_language(registry):
    with pytest.raises(ValueError, match="Unsupported target language 'zz'"):
        registry.validate_target_langs(["hi", "zz"])


def test_registry_default_voices(registry):
    # Hindi female & male defaults
    assert registry.get_default_voice("hi", gender="female") in ["Kavya", "Suhani"]
    assert registry.get_default_voice("hi", gender="male") == "Amit"

    # Bengali female & male defaults
    assert registry.get_default_voice("bn", gender="female") == "Ishita"
    assert registry.get_default_voice("bn", gender="male") == "Sourav"


def test_registry_custom_voice_override(registry):
    custom = {"hi": {"female": "CustomKavya"}}
    assert registry.get_default_voice("hi", gender="female", custom_voices=custom) == "CustomKavya"
