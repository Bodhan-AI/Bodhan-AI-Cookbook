"""Romanized-phrase glosses: validated before use, and used only for the languages that need them."""

from bodhan_dub.glossary import Term
from bodhan_dub.llm import LLMError
from bodhan_dub.repair import Unit
from bodhan_dub.romanized import add_glosses, check_gloss, text_for

LINE = "Today we make paneer tikka for Diwali. It is ekdum mast and bilkul simple, chalo shuru karte hain."
GLOSS = "Today we make paneer tikka for Diwali. It is really great and very simple, let's get started."


def unit(uid, text):
    return Unit(id=uid, start=0, end=1, text=text, raw_text=text, source_ids=["s0000"])


class FixedLLM:
    def __init__(self, reply):
        self.reply = reply

    def complete_json(self, system, user):
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_a_good_gloss_is_used_for_other_languages_but_not_the_phrases_own():
    units = [unit("u0000", LINE), unit("u0001", "Plain English only.")]
    added = add_glosses(FixedLLM({"glosses": [{"id": "u0000", "language": "hi", "text": GLOSS}]}), units, [])
    assert added == 1 and units[0].gloss == GLOSS and units[1].gloss is None
    assert text_for(units[0], "ta") == GLOSS and text_for(units[0], "bn") == GLOSS
    assert text_for(units[0], "hi") == LINE and text_for(units[0], "ur") == LINE  # same spoken language
    assert text_for(units[1], "ta") == "Plain English only."


def test_glosses_that_change_more_than_the_phrases_are_rejected():
    terms = [Term("paneer tikka", "name")]
    assert check_gloss(LINE, GLOSS, terms) == ""
    assert "numbers" in check_gloss("It costs 500 rupees, bilkul.", "It costs rupees, totally.", [])
    assert "glossary terms" in check_gloss(LINE, GLOSS.replace("paneer tikka", "a snack"), terms)
    assert "rewrote" in check_gloss(LINE, "A festive dish is simple to cook and very good, so begin now please.", [])
    assert check_gloss(LINE, LINE, []) == "empty or unchanged"


def test_rejected_or_failed_glossing_leaves_lines_untouched():
    units = [unit("u0000", LINE)]
    add_glosses(FixedLLM({"glosses": [{"id": "u0000", "language": "hi", "text": "Something else entirely."}]}),
                units, [])
    assert units[0].gloss is None
    add_glosses(FixedLLM(LLMError("down")), units, [])
    assert units[0].gloss is None and text_for(units[0], "ta") == LINE
