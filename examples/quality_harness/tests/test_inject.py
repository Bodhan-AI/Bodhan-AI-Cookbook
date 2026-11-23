import random
import unicodedata

from bodhan_eval.clients import Translation
from bodhan_eval.inject import (
    addition, format_error, hard_negative, inject, llm_edit, number, omission, wrong_language,
)

HI = {"id": "a", "lang": "hi", "source": "Water boils at 100 °C. It freezes at 0 °C.",
      "translation": "पानी 100 °C पर उबलता है। यह 0 °C पर जमता है।", "translation_origin": "bodhan"}
TA = {"id": "a", "lang": "ta", "source": HI["source"], "translation": "நீர் 100 °C இல் கொதிக்கிறது. இது 0 °C இல் உறைகிறது."}
MD = {"id": "m", "lang": "hi", "source": "## Care\n\n1. Keep it **clean**.\n\n| A | B |\n|---|---|\n| x | y |",
      "translation": "## देखभाल\n\n1. इसे **साफ** रखें।\n\n| क | ख |\n|---|---|\n| x | y |"}
ONE = {"id": "o", "lang": "hi", "source": "Tea, which is made from leaves, is a popular drink in India.",
       "translation": "चाय, जो पत्तियों से बनती है और बहुत पुरानी है, भारत में एक लोकप्रिय पेय है।"}


def rng():
    return random.Random(0)


def test_omission_deletes_a_sentence_or_clause_and_points_at_the_gap():
    new, lab = omission(HI, rng())
    assert lab["op"] == "delete_sentence" and lab["detail"]["deleted"].strip() not in new
    assert lab["span"][0] == lab["span"][1] and new[:lab["span"][0]] == HI["translation"][:lab["span"][0]]
    new, lab = omission(ONE, rng())
    assert lab["op"] == "delete_clause" and lab["detail"]["deleted"].startswith(",") and len(new) < len(ONE["translation"])


def test_addition_inserts_another_items_sentence_at_its_span():
    pool = {"hi": [("other source", "यह एक दूसरी जगह से लिया गया वाक्य है।")]}
    new, lab = addition(HI, rng(), pool=pool)
    a, b = lab["span"]
    assert new[a:b] == "यह एक दूसरी जगह से लिया गया वाक्य है।"
    assert addition(HI, rng(), pool={"hi": [(HI["source"], "same source: never used")]}) is None


def test_number_changes_one_digit_in_its_own_script_or_drops_a_unit():
    native = dict(HI, translation="पानी १०० °C पर उबलता है।")
    for seed in range(20):
        new, lab = number(native, random.Random(seed))
        if lab["op"] == "change_digit":
            a, b = lab["span"]
            assert new[a:b] != native["translation"][a:b] and unicodedata.name(new[a]).startswith("DEVANAGARI DIGIT")
        else:
            assert lab["op"] == "drop_unit" and "°C" not in new
    assert number(dict(HI, translation="कोई संख्या नहीं"), rng()) is None


def test_format_errors_break_one_element():
    seen = set()
    for seed in range(30):
        new, lab = format_error(MD, random.Random(seed))
        seen.add(lab["op"])
        assert len(new) == len(MD["translation"]) - len(lab["detail"]["deleted"])
        assert new[:lab["span"][0]] == MD["translation"][:lab["span"][0]]
    assert seen == {"drop_heading", "drop_list_marker", "drop_closing_bold", "drop_table_pipe"}
    tex = dict(HI, translation="ऊर्जा $E = mc^2$ है।")
    assert format_error(tex, rng())[0] == "ऊर्जा $E = mc^2 है।"
    assert format_error(HI, rng()) is None


def test_wrong_language_swaps_in_english_or_another_language():
    by_key = {"a": {"hi": HI, "ta": TA}}
    ops = set()
    for seed in range(20):
        new, lab = wrong_language(HI, random.Random(seed), by_key=by_key)
        a, b = lab["span"]
        assert new[a:b] in ("Water boils at 100 °C.", "It freezes at 0 °C.", "நீர் 100 °C இல் கொதிக்கிறது.", "இது 0 °C இல் உறைகிறது.")
        assert lab["severity"] == "major"  # one of two sentences
        ops.add(lab["op"])
    assert ops == {"english", "copy_ta"}


def test_hard_negatives_change_form_not_content():
    for seed in range(20):
        op, new = hard_negative(HI, random.Random(seed))
        assert new != HI["translation"]
        if op == "native_digits":
            assert "१००" in new and "०" in new
    op, new = hard_negative(dict(MD, translation="1. मूल्य 5 है"), random.Random(1))
    assert not new.startswith("१")  # list markers keep ASCII digits


class FakeClients:
    def __init__(self, edited):
        self.edited, self.translated = edited, []

    def llm(self, messages):
        return {"sentence": self.edited, "from": "x", "to": "y"}

    def translate(self, text, lang):
        self.translated.append(text)
        return Translation("पानी 100 °C पर जमता है।", "stop")


def test_llm_edit_retranslates_one_sentence_and_labels_the_diff():
    clients = FakeClients("Water freezes at 100 °C.")
    for seed in range(10):
        out = llm_edit("mistranslation")(HI, random.Random(seed), clients=clients)
        if out and out[1]["detail"]["english_before"].startswith("Water"):
            break
    new, lab = out
    a, b = lab["span"]
    assert new.startswith("पानी 100 °C पर जमता है।") and new[a:b] == "जमता"
    assert clients.translated[-1] == "Water freezes at 100 °C."
    # An edit that rewrites the whole sentence, or isn't English, is rejected.
    assert llm_edit("mistranslation")(HI, rng(), clients=FakeClients("Completely different words here now, with many new ones.")) is None
    assert llm_edit("mistranslation")(HI, rng(), clients=FakeClients("पानी")) is None
    assert llm_edit("term")(HI, rng(), clients=clients) is None  # no names or terms in the source


def test_inject_is_seeded_and_keeps_bases():
    records = [HI, TA, MD, ONE]
    first = inject(records, FakeClients("Water freezes at 100 °C."), per_category=2, negatives=2, seed=1)
    again = inject(records, FakeClients("Water freezes at 100 °C."), per_category=2, negatives=2, seed=1)
    assert first == again
    bases = [r for r in first if "base" not in r]
    assert [(r["id"], r["lang"]) for r in bases] == [("a", "hi"), ("a", "ta"), ("m", "hi"), ("o", "hi")]
    variants = [r for r in first if "base" in r]
    assert len({(v["id"], v["lang"]) for v in variants}) == len(variants)  # unique ids
    for v in variants:
        assert v["id"].startswith(v["base"] + "~")
        assert (v["labels"] == []) == (v["injection"]["category"] == "clean")
        if v["labels"]:
            assert v["labels"][0]["annotator"] == "injector" and v["labels"][0]["type"] == v["injection"]["category"]


def test_number_leaves_list_markers_alone():
    for seed in range(10):
        new, lab = number(dict(HI, translation="1. चेन साफ रखें।\n2. हर 7 दिन देखें।"), random.Random(seed))
        assert new.startswith("1.") and "\n2." in new
