import copy
import json
from pathlib import Path

import httpx2

from bodhan_eval.backtrans import check_back_translation
from bodhan_eval.clients import Clients
from bodhan_eval.config import Config
from test_clients import KEYS, TRANSLATE_OK

SOURCE = "The museum opens at 10 am. Entry costs 50 rupees for adults. Children enter free."
GOOD = "संग्रहालय सुबह 10 बजे खुलता है। वयस्कों के लिए प्रवेश शुल्क 50 रुपये है। बच्चों का प्रवेश निःशुल्क है।"
FRESH = "संग्रहालय 10 बजे खुलता है। वयस्कों के लिए प्रवेश 50 रुपये है। बच्चे निःशुल्क प्रवेश करते हैं।"  # Bodhan's own

# What the fake Indic-Translate returns for each (input, target). Back-translations are English.
GOOD_BACK = ["The museum opens at 10 am.", "Entry costs 50 rupees for adults.", "Children enter free."]
FRESH_BACK = ["The museum opens at 10.", "Entry is 50 rupees for adults.", "Children enter for free."]


class FakeTranslate:
    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, request):
        body = json.loads(request.content)
        key = (body["messages"][0]["content"], body["target_language_code"])
        self.calls.append(key)
        r = copy.deepcopy(TRANSLATE_OK[1])
        r["choices"][0]["message"]["content"] = self.table[key]
        return httpx2.Response(200, json=r)


def sentences(text):
    return [s + "।" for s in text.split("।") if s.strip()]


def setup(tmp_path, translation, back_whole, back_parts, fresh=FRESH, fresh_back=FRESH_BACK):
    table = {
        (SOURCE, "hi"): fresh,
        (translation, "en"): " ".join(back_whole),
        (fresh, "en"): " ".join(fresh_back),
    }
    for sent, back in zip(sentences(translation), back_parts):
        table[(sent.strip(), "en")] = back
    fake = FakeTranslate(table)
    cfg = Config(dataset=Path("d"), langs=("hi",), out=Path("o"), bodhan_keys=KEYS)
    clients = Clients(cfg, tmp_path / "cache", http_client=httpx2.Client(transport=httpx2.MockTransport(fake)))
    return clients, fake


def summary(result):
    return [(c.check, c.category, c.side) for c in result.candidates]


def test_clean_translation_raises_nothing_and_needs_no_second_opinion(tmp_path):
    clients, fake = setup(tmp_path, GOOD, GOOD_BACK, GOOD_BACK)
    r = check_back_translation(SOURCE, GOOD, "hi", clients)
    assert r.candidates == [] and r.control == "independent"
    assert len(fake.calls) == 3  # system back-translation, control translation, control back-translation


def test_omission_is_confirmed_and_located_in_the_translation(tmp_path):
    omitted = "संग्रहालय सुबह 10 बजे खुलता है। बच्चों का प्रवेश निःशुल्क है।"
    back = [GOOD_BACK[0], GOOD_BACK[2]]
    clients, fake = setup(tmp_path, omitted, back, back)
    r = check_back_translation(SOURCE, omitted, "hi", clients)
    (c,) = r.candidates
    assert (c.category, c.side) == ("omission", "source") and SOURCE[slice(*c.span)] == "Entry costs 50 rupees for adults."
    s, e = c.evidence["translation_span"]
    assert s == e == omitted.index("।") + 1  # insertion point after the first sentence
    assert c.evidence["confirmed"] and c.evidence["control_score"] > 0.3
    assert len(fake.calls) == 5  # + one per translated sentence for the second opinion


def test_omission_the_control_also_loses_is_checker_noise(tmp_path):
    omitted_back = [GOOD_BACK[0], GOOD_BACK[2]]
    # The back-translation drops sentence 2 for the system AND for Bodhan's own fresh translation.
    clients, _ = setup(tmp_path, GOOD, omitted_back, GOOD_BACK, fresh_back=[FRESH_BACK[0], FRESH_BACK[2]])
    r = check_back_translation(SOURCE, GOOD, "hi", clients)
    assert r.candidates == []
    assert [x["reason"] for x in r.suppressed] == ["the control loses this sentence too"]


def test_whole_item_false_omission_is_dropped_by_the_second_opinion(tmp_path):
    clients, _ = setup(tmp_path, GOOD, [GOOD_BACK[0], GOOD_BACK[2]], GOOD_BACK)  # whole-item BT dropped it, per-sentence didn't
    r = check_back_translation(SOURCE, GOOD, "hi", clients)
    assert r.candidates == []
    assert any("sentence-by-sentence" in x["reason"] for x in r.suppressed)


def test_addition_is_located_in_the_translation(tmp_path):
    added = GOOD + " इमारत पिछले साल नीली रंगी गई थी।"
    back = GOOD_BACK + ["The building was painted blue last year."]
    clients, _ = setup(tmp_path, added, back, back)
    (c,) = check_back_translation(SOURCE, added, "hi", clients).candidates
    assert (c.category, c.side) == ("addition", "translation")
    assert added[slice(*c.span)] == "इमारत पिछले साल नीली रंगी गई थी।"


def test_mistranslation_is_a_large_drop_below_the_control(tmp_path):
    wrong = "संग्रहालय सुबह 10 बजे खुलता है। नदी ने पुराने पुल को डुबो दिया। बच्चों का प्रवेश निःशुल्क है।"
    back = [GOOD_BACK[0], "The river flooded the old bridge.", GOOD_BACK[2]]
    clients, _ = setup(tmp_path, wrong, back, back)
    (c,) = check_back_translation(SOURCE, wrong, "hi", clients).candidates
    assert (c.category, c.side) == ("mistranslation", "translation")
    assert wrong[slice(*c.span)] == "नदी ने पुराने पुल को डुबो दिया।"
    assert SOURCE[slice(*c.evidence["source_span"])] == "Entry costs 50 rupees for adults."


def test_missing_phrase_counts_only_when_the_control_kept_it(tmp_path):
    short = "संग्रहालय सुबह 10 बजे खुलता है। प्रवेश शुल्क 50 रुपये है। बच्चों का प्रवेश निःशुल्क है।"
    back = [GOOD_BACK[0], "Entry costs 50 rupees.", GOOD_BACK[2]]
    clients, _ = setup(tmp_path, short, back, back)
    (c,) = check_back_translation(SOURCE, short, "hi", clients).candidates
    assert (c.check, c.category) == ("back_translation_phrase", "omission")
    assert SOURCE[slice(*c.span)] == "for adults"

    # Same system output, but the control's back-translation also lacks "adults": a paraphrase habit, not an error.
    clients, _ = setup(tmp_path / "2", short, back, back, fresh_back=[FRESH_BACK[0], "Entry is 50 rupees.", FRESH_BACK[2]])
    r = check_back_translation(SOURCE, short, "hi", clients)
    assert r.candidates == [] and r.suppressed[0]["category"] == "phrase"


def test_generated_translation_has_no_independent_control(tmp_path):
    omitted = "संग्रहालय सुबह 10 बजे खुलता है। बच्चों का प्रवेश निःशुल्क है।"
    back = [GOOD_BACK[0], GOOD_BACK[2]]
    clients, fake = setup(tmp_path, omitted, back, back)
    r = check_back_translation(SOURCE, omitted, "hi", clients, generated=True)
    assert r.control == "same_as_system"
    (c,) = r.candidates
    assert c.category == "omission" and c.score < 0.8  # only the second opinion vouches for it
    assert (SOURCE, "hi") not in fake.calls  # no control translation requested


def test_weak_pair_is_an_omission_when_words_were_only_taken_away(tmp_path):
    cut = "संग्रहालय सुबह 10 बजे खुलता है। प्रवेश है। बच्चों का प्रवेश निःशुल्क है।"
    back = [GOOD_BACK[0], "There is entry.", GOOD_BACK[2]]
    clients, _ = setup(tmp_path, cut, back, back)
    (c,) = check_back_translation(SOURCE, cut, "hi", clients).candidates
    assert c.category == "omission" and c.side == "translation" and cut[slice(*c.span)] == "प्रवेश है।"
    assert [ch["source_words"] for ch in c.evidence["changes"]] == ["costs 50 rupees for adults"]


def test_added_negation_is_a_mistranslation_unless_the_control_has_it_too(tmp_path):
    flipped = "संग्रहालय सुबह 10 बजे खुलता है। वयस्कों के लिए प्रवेश शुल्क 50 रुपये है। बच्चों का प्रवेश निःशुल्क नहीं है।"
    back = ["The museum opens at 10 am.", "Entry costs 50 rupees for adults.", "Children do not enter free."]
    clients, _ = setup(tmp_path, flipped, back, back)
    r = check_back_translation(SOURCE, flipped, "hi", clients)
    (neg,) = [c for c in r.candidates if c.check == "back_translation_negation"]
    assert neg.category == "mistranslation" and SOURCE[neg.span[0]:neg.span[1]] == "Children enter free."
    assert neg.evidence["negations"] == {"source": 0, "back_translation": 1, "control": 0}

    # The control's back-translation adds the same "not": the checker's habit, not the system's error.
    clients, _ = setup(tmp_path / "2", flipped, back, back, fresh_back=FRESH_BACK[:2] + ["Children do not pay to enter."])
    r = check_back_translation(SOURCE, flipped, "hi", clients)
    assert not [c for c in r.candidates if c.check == "back_translation_negation"]
    assert any("same negations" in s["reason"] for s in r.suppressed)
