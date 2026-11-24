from __future__ import annotations

import pytest

from bodhan_dub.glossary import Glossary


@pytest.fixture
def glossary(tmp_path) -> Glossary:
    path = tmp_path / "glossary.yaml"
    path.write_text(
        """
terms:
  - en: right angle
    hi: समकोण
    ta: செங்கோணம்
  - en: hypotenuse
    hi: कर्ण
""",
        encoding="utf-8",
    )
    return Glossary.load(path)


def test_loads_terms(glossary: Glossary) -> None:
    assert len(glossary) == 2
    assert glossary.for_language("hi") == {"right angle": "समकोण", "hypotenuse": "कर्ण"}


def test_language_without_a_rendering_is_skipped(glossary: Glossary) -> None:
    assert glossary.for_language("bn") == {}


def test_missing_file_is_an_empty_glossary(tmp_path) -> None:
    assert len(Glossary.load(tmp_path / "nope.yaml")) == 0
    assert len(Glossary.load(None)) == 0


def test_terms_in_text_is_case_insensitive(glossary: Glossary) -> None:
    assert glossary.terms_in("A Right Angle is ninety degrees") == ["right angle"]


def test_consistency_passes_when_the_term_is_rendered(glossary: Glossary) -> None:
    sources = {0: "a right angle is ninety degrees"}
    translations = {0: "समकोण नब्बे डिग्री का होता है"}

    assert glossary.check(sources, translations, "hi") == []


def test_consistency_flags_a_missing_rendering(glossary: Glossary) -> None:
    sources = {0: "a right angle", 1: "another right angle here"}
    translations = {0: "समकोण", 1: "यहाँ एक लंब कोण है"}

    issues = glossary.check(sources, translations, "hi")

    assert len(issues) == 1
    assert issues[0]["term"] == "right angle"
    assert issues[0]["segments_missing"] == [1]
    assert 0 in issues[0]["segments_with_term"]


def test_consistency_tolerates_inflection(glossary: Glossary) -> None:
    sources = {0: "two right angle"}
    # Plural oblique form of the same term.
    translations = {0: "दो समकोणों"}

    assert glossary.check(sources, translations, "hi") == []


def test_consistency_ignores_segments_without_the_term(glossary: Glossary) -> None:
    assert glossary.check({0: "unrelated sentence"}, {0: "कुछ और"}, "hi") == []


def test_serialises_for_the_report(glossary: Glossary) -> None:
    payload = glossary.to_dict()
    assert payload[0]["en"] == "right angle"
    assert payload[0]["targets"]["ta"] == "செங்கோணம்"
