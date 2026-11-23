from __future__ import annotations

import pytest

from bodhan_dub.config import DEFAULT_BASE_URL, load_settings
from bodhan_dub.errors import ConfigError


def test_canonical_name_is_read() -> None:
    settings = load_settings({"BODHAN_TRANSCRIBE_KEY": "k1"}, dotenv=False)
    assert settings.key_for("transcribe") == "k1"


def test_api_key_alias_is_accepted() -> None:
    settings = load_settings({"BODHAN_TRANSCRIBE_API_KEY": "alias"}, dotenv=False)
    assert settings.key_for("transcribe") == "alias"


def test_canonical_name_beats_the_alias() -> None:
    settings = load_settings(
        {"BODHAN_TRANSCRIBE_KEY": "canonical", "BODHAN_TRANSCRIBE_API_KEY": "alias"},
        dotenv=False,
    )
    assert settings.key_for("transcribe") == "canonical"


def test_generic_key_is_the_fallback() -> None:
    settings = load_settings({"BODHAN_API_KEY": "shared"}, dotenv=False)
    for capability in ("transcribe", "translate", "speak", "ocr"):
        assert settings.key_for(capability) == "shared"


def test_per_model_key_beats_the_generic_key() -> None:
    settings = load_settings(
        {"BODHAN_API_KEY": "shared", "BODHAN_SPEAK_KEY": "own"}, dotenv=False
    )
    assert settings.key_for("speak") == "own"
    assert settings.key_for("transcribe") == "shared"


def test_missing_key_names_every_accepted_variable() -> None:
    settings = load_settings({}, dotenv=False)
    with pytest.raises(ConfigError) as caught:
        settings.key_for("transcribe")
    message = str(caught.value)
    assert "BODHAN_TRANSCRIBE_KEY" in message
    assert "BODHAN_TRANSCRIBE_API_KEY" in message


def test_public_dict_never_contains_key_values() -> None:
    settings = load_settings(
        {"BODHAN_API_KEY": "super-secret", "BODHAN_SPEAK_KEY": "another-secret"},
        dotenv=False,
    )
    public = settings.to_public_dict()
    rendered = repr(public)
    assert "super-secret" not in rendered
    assert "another-secret" not in rendered
    assert public["keys_present"]["speak"] is True


def test_base_url_default_and_override() -> None:
    assert load_settings({}, dotenv=False).base_url == DEFAULT_BASE_URL
    assert load_settings({"BODHAN_BASE_URL": "https://example.test/"}, dotenv=False).base_url == (
        "https://example.test"
    )


def test_llm_is_disabled_unless_all_three_values_are_present() -> None:
    partial = load_settings({"LLM_BASE_URL": "https://x.test", "LLM_MODEL": "m"}, dotenv=False)
    assert partial.llm is None

    complete = load_settings(
        {"LLM_BASE_URL": "https://x.test", "LLM_MODEL": "m", "LLM_API_KEY": "k"},
        dotenv=False,
    )
    assert complete.llm is not None
    assert complete.llm.enabled is True


def test_concurrency_floor_is_one() -> None:
    settings = load_settings({"BODHAN_DUB_MAX_INFLIGHT": "0"}, dotenv=False)
    assert settings.max_inflight == 1


def test_bad_numeric_override_is_a_clear_error() -> None:
    with pytest.raises(ConfigError, match="must be a number"):
        load_settings({"BODHAN_DUB_TIMEOUT_S": "soon"}, dotenv=False)


def test_rpm_defaults_to_120_and_is_configurable() -> None:
    assert load_settings({}, dotenv=False).rpm == 120
    assert load_settings({"BODHAN_DUB_RPM": "60"}, dotenv=False).rpm == 60
    assert load_settings({"BODHAN_DUB_RPM": "0"}, dotenv=False).rpm == 1
