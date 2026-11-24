from __future__ import annotations

import pytest

from bodhan_dub import languages


def test_parses_a_list() -> None:
    assert languages.parse_langs("hi,ta,bn") == ["hi", "ta", "bn"]


def test_trims_spaces_and_case() -> None:
    assert languages.parse_langs(" HI , ta ") == ["hi", "ta"]


def test_deduplicates_preserving_order() -> None:
    assert languages.parse_langs("hi,bn,hi") == ["hi", "bn"]


def test_rejects_empty() -> None:
    with pytest.raises(ValueError, match="empty"):
        languages.parse_langs("  ,  ")


def test_rejects_unknown_with_the_supported_list() -> None:
    with pytest.raises(ValueError) as caught:
        languages.parse_langs("fr")
    message = str(caught.value)
    assert "fr" in message
    assert "hi" in message


def test_rejects_bcp47_style_codes() -> None:
    with pytest.raises(ValueError):
        languages.parse_langs("hi" + "-IN")


def test_every_dubbable_language_has_a_voice() -> None:
    for code in languages.DUBBABLE_LANGS:
        language = languages.get(code)
        assert language.voice("female")
        assert language.voice("male")


def test_voice_falls_back_rather_than_failing() -> None:
    language = languages.get("hi")
    assert language.voice("female", preferred="Amit") == "Amit"
    assert language.voice("female", preferred="NotARealVoice") == "Kavya"


def test_tamil_and_bengali_voices_match_the_library() -> None:
    assert languages.get("ta").voice("female") == "Anitha"
    assert languages.get("bn").voice("male") == "Sourav"


def test_prompt_names_are_language_names_not_codes() -> None:
    assert languages.prompt_name("hi") == "Hindi"
    assert languages.prompt_name("ta") == "Tamil"
    assert languages.prompt_name("bn") == "Bengali"
    assert languages.prompt_name("mr") == "Marathi"


def test_prompt_names_disambiguate_multi_script_languages() -> None:
    assert "Perso-Arabic" in languages.prompt_name("ks")
    assert "Meitei" in languages.prompt_name("mni")
    assert "Devanagari" in languages.prompt_name("sd")


def test_prompt_name_supports_script_suffixes() -> None:
    assert languages.prompt_name("hi", "codemix") == "Hindi (code-mixed)"
    assert languages.prompt_name("hi", "roman") == "Hindi (Roman)"
    assert languages.prompt_name("hi", "native") == "Hindi"


def test_every_dubbable_language_has_a_prompt_name() -> None:
    for code in languages.DUBBABLE_LANGS:
        name = languages.prompt_name(code)
        assert name and name != code


def test_terminator_matches_the_script() -> None:
    assert languages.terminator("hi") == "।"
    assert languages.terminator("mr") == "।"
    assert languages.terminator("ta") == "."
    assert languages.terminator("bn") == "."


def test_iso3_codes_for_subtitle_tracks() -> None:
    assert languages.iso3("hi") == "hin"
    assert languages.iso3("ta") == "tam"
    assert languages.iso3("bn") == "ben"
    assert languages.iso3("zz") == "zz"


def test_name_lookup() -> None:
    assert languages.name("hi") == "Hindi"
    assert languages.name("zz") == "zz"
