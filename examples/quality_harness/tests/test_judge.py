import pytest

from bodhan_eval.clients import ModelError
from bodhan_eval.judge import judge

SOURCE = "Exports rose to $25 million. Bananas were the main crop."
HI = "निर्यात बढ़कर 2.5 करोड़ डॉलर हो गया। केले मुख्य फसल थे।"
IMPORTS = "आयात बढ़कर 2.5 करोड़ डॉलर हो गया। केले मुख्य फसल थे।"


class FakeLLM:
    def __init__(self, reply):
        self.reply, self.messages = reply, []

    def llm(self, messages, repeat=0):
        self.messages.append((messages, repeat))
        return self.reply


def run(reply, translation=IMPORTS):
    fake = FakeLLM(reply)
    return judge(SOURCE, translation, "Imports rose to 25 million dollars. Bananas were the main crop.", "hi", fake), fake


def test_quotes_become_spans_and_the_prompt_is_blind():
    (cands, info), fake = run({"errors": [
        {"category": "mistranslation", "severity": "major", "side": "translation", "segment": "T1", "quote": "आयात",
         "explanation": "says imports (आयात), not exports", "confidence": "high"},
        {"category": "omission", "severity": "minor", "side": "source", "segment": "S2", "quote": "main",
         "explanation": "x", "confidence": "low"},
    ]})
    assert [(c.category, c.side, IMPORTS[c.span[0]:c.span[1]] if c.side == "translation" else SOURCE[c.span[0]:c.span[1]])
            for c in cands] == [("mistranslation", "translation", "आयात"), ("omission", "source", "main")]
    assert cands[0].check == "judge" and cands[0].score == 0.8 and cands[0].severity == "major"
    assert info == {"errors": 2, "kept": 2, "dropped": [], "stripped_words": 0}
    user = fake.messages[0][0][1]["content"]
    assert "S1: Exports rose to $25 million." in user and "T2: केले मुख्य फसल थे।" in user and "Hindi" in user
    assert "candidate" not in user.lower()  # no hints from the other checks


def test_invented_text_is_dropped_and_indic_in_explanations_removed():
    (cands, info), _ = run({"errors": [
        {"category": "mistranslation", "severity": "major", "side": "translation", "segment": "T1",
         "quote": "निर्यात", "explanation": "x"},  # not in this translation: the LLM wrote it, so it's dropped
        {"category": "term_entity", "severity": "huge", "side": "translation", "segment": "T9",
         "quote": "मुख्य  फसल", "explanation": "should be प्रमुख फसल, not मुख्य"},
        {"category": "style", "quote": "केले"},
    ]})
    assert [d["reason"] for d in info["dropped"]] == ["invalid_quote", "unknown category 'style'"]
    (c,) = cands
    assert c.category == "term" and c.severity == "major"  # alias mapped, bad severity defaulted
    assert IMPORTS[c.span[0]:c.span[1]] == "मुख्य फसल"  # found despite the doubled space, in the right segment
    assert c.evidence["explanation"] == "should be […] फसल, not मुख्य" and info["stripped_words"] == 1


def test_side_is_corrected_when_the_quote_is_on_the_other_side():
    (cands, _), _ = run({"errors": [{"category": "omission", "severity": "major", "side": "translation", "quote": "Bananas"}]})
    assert cands[0].side == "source" and SOURCE[cands[0].span[0]:cands[0].span[1]] == "Bananas"


def test_reply_without_an_errors_list_is_an_error():
    with pytest.raises(ModelError):
        run({"verdict": "fine"})
    (cands, info), _ = run({"errors": []}, translation=HI)
    assert cands == [] and info["kept"] == 0
