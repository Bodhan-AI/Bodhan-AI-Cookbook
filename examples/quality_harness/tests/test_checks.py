from bodhan_eval.checks import all_checks
from bodhan_eval.checks.registry import CheckContext
from bodhan_eval.config import load_languages, load_thresholds
from bodhan_eval.models import Item

LANGS = load_languages()
THR = load_thresholds()


def _run(item, artifacts=None):
    ctx = CheckContext(item=item, lang=item.lang, thresholds=THR, languages=LANGS,
                       artifacts=artifacts or {})
    out = []
    for spec in all_checks():
        out.extend(spec.fn(ctx))
    return out


def _cats(evs):
    return {e.category for e in evs}


def test_number_mismatch_flagged():
    it = Item(id="x", source="Water boils at 100 degrees.", lang="hi",
              translation="पानी 90 डिग्री पर उबलता है।")
    evs = _run(it)
    assert "number_unit" in _cats(evs)
    strong = [e for e in evs if e.category == "number_unit" and e.strength == "strong"]
    assert strong and strong[0].source_span is not None


def test_untranslated_detected():
    it = Item(id="x", source="Hello world", lang="hi", translation="Hello world")
    evs = _run(it)
    assert "wrong_language_script" in _cats(evs)


def test_unchanged_acronym_or_number_is_not_called_untranslated():
    for text in ("DNA", "100", "$x^2$"):
        it = Item(id="x", source=text, lang="hi", translation=text)
        assert "wrong_language_script" not in _cats(_run(it))


def test_clean_item_no_strong_evidence():
    it = Item(id="x", source="The sun is a star.", lang="ta",
              translation="சூரியன் ஒரு நட்சத்திரம்.")
    evs = _run(it)
    strong = [e for e in evs if not e.noisy and e.strength == "strong"]
    assert not strong


def test_format_break_detected():
    it = Item(id="x", source="Area is $A=\\pi r^2$ now.", lang="ta",
              translation="பரப்பு $A=\\pi r^2 இப்போது.")
    evs = _run(it)
    assert "format" in _cats(evs)


def test_latin_number_not_flagged_as_wrong_script():
    it = Item(id="x", source="DNA has 4 bases.", lang="hi",
              translation="DNA में 4 क्षार होते हैं।")
    evs = _run(it)
    # DNA is a legitimate acronym; should not trigger wrong_language_script
    assert "wrong_language_script" not in _cats(evs)
