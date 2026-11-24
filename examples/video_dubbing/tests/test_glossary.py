"""Glossary: hallucinated terms are dropped, user terms win, bad YAML fails clearly."""

import pytest

from bodhan_dub.glossary import (
    build_glossary,
    count_occurrences,
    filter_llm_terms,
    load_user_glossary,
)
from bodhan_dub.llm import LLMError
from bodhan_dub.repair import Unit

TEXT = "Angle AOC and angle COB are a linear pair. A linear pair adds to 180 degrees. OA meets OB."


def unit(text, dropped=None):
    return Unit(id="u0000", start=0, end=1, text=text, raw_text=text, source_ids=["s0000"], dropped=dropped)


class FixedLLM:
    def __init__(self, reply):
        self.reply = reply

    def complete_json(self, system, user):
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_occurrences_are_word_bounded_and_short_labels_case_sensitive():
    assert count_occurrences("linear pair", TEXT) == 2
    assert count_occurrences("OA", TEXT) == 1
    assert count_occurrences("oa", TEXT) == 0
    assert count_occurrences("OC", TEXT) == 0  # inside "AOC", not a word of its own


def test_terms_not_in_the_text_are_dropped_and_duplicates_merged():
    reply = {"terms": [{"term": "linear pair", "kind": "technical"}, {"term": "Linear Pair", "kind": "technical"},
                       {"term": "protractor", "kind": "technical"}, {"term": "AOC", "kind": "label"},
                       {"term": "COB", "kind": "nonsense-kind"}, "junk"]}
    terms = filter_llm_terms(reply, TEXT)
    assert [(t.term, t.occurrences) for t in terms] == [("linear pair", 2), ("AOC", 1)]


def test_user_terms_win_and_llm_failure_is_not_fatal():
    user, _ = load_user_glossary_from_text("terms:\n  - term: linear pair\n    keep: true\n  - Bodhan\n")
    terms = build_glossary(FixedLLM(LLMError("down")), [unit(TEXT)], user)
    assert {t.term: (t.source, t.keep) for t in terms} == {"linear pair": ("user", True), "Bodhan": ("user", False)}

    terms = build_glossary(FixedLLM({"terms": [{"term": "linear pair", "kind": "technical"},
                                               {"term": "AOC", "kind": "label"}]}), [unit(TEXT)], user)
    by_term = {t.term: t for t in terms}
    assert by_term["linear pair"].source == "user" and by_term["AOC"].source == "llm"


def test_dropped_units_are_ignored():
    terms = build_glossary(FixedLLM({"terms": [{"term": "chorus", "kind": "technical"}]}),
                           [unit(TEXT), unit("la la chorus", dropped="music")])
    assert terms == []


@pytest.mark.parametrize("yaml_text, expected", [
    ("terms:\n  - linear pair\n  - {term: Bodhan, keep: true}\n", {"linear pair": False, "Bodhan": True}),
    ("- linear pair\n- {term: GDP, kind: acronym}\n", {"linear pair": False, "GDP": False}),
    ("linear pair:\nBodhan: keep\nGDP: acronym\n", {"linear pair": False, "Bodhan": True, "GDP": False}),
])
def test_glossary_accepts_several_shapes(tmp_path, yaml_text, expected):
    terms, warnings = load_user_glossary_from_text(yaml_text, tmp_path)
    assert {t.term: t.keep for t in terms} == expected and warnings == []


def test_glossary_renderings_are_kept_for_checking():
    terms, _ = load_user_glossary_from_text("linear pair: {hi: रैखिक युग्म, ta: நேரியல் ஜோடி}\n")
    assert terms[0].renderings == {"hi": "रैखिक युग्म", "ta": "நேரியல் ஜோடி"}


@pytest.mark.parametrize("yaml_text, message", [
    ("terms: [unclosed", "unreadable"),
    ("42", "expected a list or mapping"),
    ("terms:\n  - kind: label\n", "has no term"),
    ("terms:\n  - term: x\n    kind: verb\n", "unknown kind"),
])
def test_bad_glossary_is_a_warning_not_a_failure(tmp_path, yaml_text, message):
    _, warnings = load_user_glossary_from_text(yaml_text, tmp_path)
    assert any(message in w for w in warnings), warnings


def load_user_glossary_from_text(text, tmp_path=None):
    import tempfile
    from pathlib import Path

    folder = Path(tmp_path or tempfile.mkdtemp())
    path = folder / "glossary.yaml"
    path.write_text(text, encoding="utf-8")
    return load_user_glossary(path)
