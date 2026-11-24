from __future__ import annotations

import pytest

from bodhan_agent.errors import ToolError
from bodhan_agent.settings import Settings


def _disable_dotenv_autoload(monkeypatch):
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)


def test_settings_loads_model_keys_and_llm_configuration(monkeypatch, tmp_path):
    _disable_dotenv_autoload(monkeypatch)
    monkeypatch.chdir(tmp_path)
    values = {
        "BODHAN_TRANSCRIBE_KEY": "transcribe",
        "BODHAN_TRANSLATE_KEY": "translate",
        "BODHAN_SPEAK_KEY": "speak",
        "BODHAN_OCR_KEY": "ocr",
        "LLM_BASE_URL": "https://llm.example/v1",
        "LLM_MODEL": "tools-model",
        "LLM_API_KEY": "llm-secret",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    settings = Settings.from_env(require_llm=True, required_models=("transcribe", "translate", "speak", "ocr"))

    assert settings.transcribe_key == "transcribe"
    assert settings.translate_key == "translate"
    assert settings.speak_key == "speak"
    assert settings.ocr_key == "ocr"
    assert settings.llm_base_url == "https://llm.example/v1"
    assert settings.llm_model == "tools-model"
    assert settings.llm_api_key == "llm-secret"


def test_settings_reports_missing_required_model_key(monkeypatch, tmp_path):
    _disable_dotenv_autoload(monkeypatch)
    monkeypatch.chdir(tmp_path)
    for key in ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ToolError, match="BODHAN_TRANSLATE_KEY") as error:
        Settings.from_env(required_models=("translate",))
    assert error.value.code == "missing_key"


def test_settings_requires_all_llm_values_when_agent_enabled(monkeypatch, tmp_path):
    _disable_dotenv_autoload(monkeypatch)
    monkeypatch.chdir(tmp_path)
    for key in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ToolError, match="Set LLM_BASE_URL, LLM_MODEL and LLM_API_KEY") as error:
        Settings.from_env(require_llm=True)
    assert error.value.code == "missing_llm_config"


def test_settings_normalizes_bodhan_base_url(monkeypatch, tmp_path):
    _disable_dotenv_autoload(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BODHAN_BASE_URL", "https://custom.example/root/")
    settings = Settings.from_env()
    assert settings.api_base_url == "https://custom.example/root"
