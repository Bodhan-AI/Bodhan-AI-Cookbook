from __future__ import annotations

from pathlib import Path

import pytest

from bodhan_eval.clients import KEY_ENV
from bodhan_eval.config import Settings, canonical_category, canonical_severity, lang_info, normalize_lang_code


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("hi", "hi", id="already-canonical"),
        pytest.param("HI", "hi", id="upper-case"),
        pytest.param(" ta ", "ta", id="whitespace"),
        pytest.param("hi-IN", "hi", id="region-subtag"),
        pytest.param("bn_in", "bn", id="underscore-region"),
        pytest.param("ks-Deva", "ks-Deva", id="script-variant-kept"),
        pytest.param("KS-DEVA", "ks-Deva", id="script-variant-case"),
        pytest.param("mni-beng", "mni-Beng", id="script-variant-lower"),
        pytest.param("sd-Arab-PK", "sd-Arab", id="script-variant-with-region"),
        pytest.param("sd-Deva", "sd", id="unknown-script-falls-back-to-base"),
        pytest.param("xx", "xx", id="unknown-code-unchanged"),
        pytest.param("xx-YY", "xx-YY", id="unknown-code-with-region-unchanged"),
    ],
)
def test_normalize_lang_code(raw: str, expected: str) -> None:
    assert normalize_lang_code(raw) == expected


def test_unknown_language_still_has_info() -> None:
    info = lang_info("xx")
    assert info.known is False
    assert info.script is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Omission", "omission"), ("number or unit", "number"), ("no-such-type", None), (None, None)],
)
def test_canonical_category(raw: str | None, expected: str | None) -> None:
    assert canonical_category(raw) == expected


def test_canonical_severity_defaults_to_major() -> None:
    assert canonical_severity(None) == "major"
    assert canonical_severity("weird") == "major"


BODHAN_KEY_VARS = [*KEY_ENV.values(), "BODHAN_API_KEY"]


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        pytest.param({"BODHAN_TRANSLITERATE_KEY": "own", "BODHAN_API_KEY": "any", "BODHAN_TRANSLATE_KEY": "tr"}, "own",
                     id="own-key-wins"),
        pytest.param({"BODHAN_API_KEY": "any", "BODHAN_TRANSLATE_KEY": "tr"}, "any", id="single-key-next"),
        pytest.param({"BODHAN_TRANSLATE_KEY": "tr"}, "tr", id="translate-key-last"),
        pytest.param({}, None, id="none-set"),
    ],
)  # fmt: skip
def test_transliterate_key_fallback_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: dict[str, str], expected: str | None
) -> None:
    monkeypatch.chdir(tmp_path)  # from_env loads ./.env; there is none here
    for name in BODHAN_KEY_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    s = Settings.from_env()
    assert s.transliterate_key == expected
    assert s.translate_key == (env.get("BODHAN_TRANSLATE_KEY") or env.get("BODHAN_API_KEY"))  # unchanged
