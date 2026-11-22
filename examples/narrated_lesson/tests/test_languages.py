"""Unit tests for language allowlists and CLI validation."""

from __future__ import annotations

import pytest

from bodhan_lesson.languages import (
    SOURCE_LANGS,
    SPEAK_LANGS,
    TARGET_LANGS,
    parse_lang_list,
    validate_source_lang,
    validate_target_langs,
)


def test_speak_langs_include_demo_targets() -> None:
    assert {"hi", "ta", "bn", "en"} <= SPEAK_LANGS
    assert TARGET_LANGS == SPEAK_LANGS


def test_source_langs_include_script_variants() -> None:
    assert "en" in SOURCE_LANGS
    assert "ks-Deva" in SOURCE_LANGS
    assert "mni-Beng" in SOURCE_LANGS


def test_parse_lang_list_trims() -> None:
    assert parse_lang_list("hi, ta, bn") == ["hi", "ta", "bn"]


def test_validate_target_langs_ok() -> None:
    validate_target_langs(["hi", "ta", "gu"])


def test_validate_target_langs_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unsupported --langs"):
        validate_target_langs(["hi", "xx"])


def test_validate_target_langs_rejects_empty() -> None:
    with pytest.raises(ValueError, match="at least one"):
        validate_target_langs([])


def test_validate_source_lang_optional() -> None:
    validate_source_lang(None)
    validate_source_lang("en")
    validate_source_lang("hi")


def test_validate_source_lang_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="Unsupported --source-lang"):
        validate_source_lang("zz")
