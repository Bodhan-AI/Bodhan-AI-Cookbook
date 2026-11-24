"""Unit tests for Bodhan Configuration (src.common.config)."""

from __future__ import annotations

import os
import pytest

from src.common.config import BodhanConfig


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Ensure a clean environment for config tests."""
    env_vars = [
        "BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY",
        "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY", "BODHAN_API_KEY",
        "BODHAN_BASE_URL", "LLM_BASE_URL", "LLM_MODEL",
        "LLM_API_KEY", "LLM_REASONING_EFFORT",
    ]
    for var in env_vars:
        monkeypatch.delenv(var, raising=False)


def test_defaults_when_no_env_vars():
    """All fields have sensible defaults when no env vars are set."""
    cfg = BodhanConfig.from_env(load_env_file=False)
    assert cfg.transcribe_key == ""
    assert cfg.translate_key == ""
    assert cfg.speak_key == ""
    assert cfg.ocr_key == ""
    assert cfg.base_url == "https://api.bodhan.ai/v1"
    assert cfg.llm_model == "gpt-4o"
    assert cfg.llm_base_url == ""
    assert cfg.llm_api_key == ""
    assert cfg.llm_reasoning_effort == ""


def test_bodhan_api_key_is_not_a_fallback(monkeypatch):
    """BODHAN_API_KEY must not populate any per-model key."""
    monkeypatch.setenv("BODHAN_API_KEY", "shared-key")
    cfg = BodhanConfig.from_env(load_env_file=False)
    assert cfg.transcribe_key == ""
    assert cfg.translate_key == ""
    assert cfg.speak_key == ""
    assert cfg.ocr_key == ""


def test_per_model_keys_are_independent(monkeypatch):
    """Each model reads only its own key."""
    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "transcribe-key")
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "translate-key")
    monkeypatch.setenv("BODHAN_SPEAK_KEY", "speak-key")
    monkeypatch.setenv("BODHAN_OCR_KEY", "ocr-key")
    cfg = BodhanConfig.from_env(load_env_file=False)
    assert cfg.transcribe_key == "transcribe-key"
    assert cfg.translate_key == "translate-key"
    assert cfg.speak_key == "speak-key"
    assert cfg.ocr_key == "ocr-key"


def test_env_vars_override_defaults(monkeypatch):
    """Environment variables populate config fields."""
    monkeypatch.setenv("LLM_MODEL", "custom-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://gateway.example.com/v1")
    monkeypatch.setenv("LLM_API_KEY", "secret-key")
    monkeypatch.setenv("LLM_REASONING_EFFORT", "high")
    monkeypatch.setenv("BODHAN_BASE_URL", "https://custom.bodhan.ai/v1")
    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "transcribe-key")

    cfg = BodhanConfig.from_env(load_env_file=False)
    assert cfg.llm_model == "custom-model"
    assert cfg.llm_base_url == "https://gateway.example.com/v1"
    assert cfg.llm_api_key == "secret-key"
    assert cfg.llm_reasoning_effort == "high"
    assert cfg.base_url == "https://custom.bodhan.ai/v1"
    assert cfg.transcribe_key == "transcribe-key"


def test_base_url_trailing_slash_stripped(monkeypatch):
    """Trailing slashes are stripped from base URLs."""
    monkeypatch.setenv("BODHAN_BASE_URL", "https://api.bodhan.ai/v1/")
    monkeypatch.setenv("LLM_BASE_URL", "https://gateway.com/v1/")
    cfg = BodhanConfig.from_env(load_env_file=False)
    assert cfg.base_url == "https://api.bodhan.ai/v1"
    assert cfg.llm_base_url == "https://gateway.com/v1"


def test_config_is_frozen(monkeypatch):
    """Config is immutable after creation."""
    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "key")
    cfg = BodhanConfig.from_env(load_env_file=False)
    with pytest.raises(AttributeError):
        cfg.transcribe_key = "new-key"  # type: ignore
