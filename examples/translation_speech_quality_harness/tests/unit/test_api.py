"""Library entry points (``bodhan_eval.api``): no keys needed for the deterministic checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from bodhan_eval.api import Harness

KEYS = ("BODHAN_API_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_TRANSCRIBE_KEY",
        "BODHAN_TRANSLITERATE_KEY", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL")  # fmt: skip


@pytest.fixture
def harness(tmp_path: Path, monkeypatch) -> Harness:
    for k in KEYS:
        monkeypatch.setenv(k, "")
    monkeypatch.chdir(tmp_path)  # no .env here
    return Harness(cache_dir=tmp_path / "cache", max_cost_inr=0)


def test_check_translation_flags_a_changed_number(harness: Harness) -> None:
    flags = harness.check_translation("Water boils at 100 °C.", "पानी 200 °C पर उबलता है।", "hi")
    assert [(f.category, f.text) for f in flags] == [("number", "200")]


def test_check_translation_passes_a_correct_one(harness: Harness) -> None:
    assert harness.check_translation("Water boils at 100 °C.", "पानी 100 °C पर उबलता है।", "hi") == []


def test_api_never_imports_the_cli() -> None:
    import bodhan_eval.api as api
    import bodhan_eval.pipeline as pipeline

    for mod in (api, pipeline):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "from .cli import" not in src and "bodhan_eval.cli" not in src
