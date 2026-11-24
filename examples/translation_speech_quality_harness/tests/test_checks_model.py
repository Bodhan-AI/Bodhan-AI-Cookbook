"""Phase 3 model checks with fake clients: segment alignment, chrF++ reference, back-translation."""

import asyncio
from dataclasses import dataclass

from bodhan_eval.align import align_segments
from bodhan_eval.checks.backtranslation import check_backtranslation, reconcile_numbers
from bodhan_eval.checks.reference import check_reference, similarity
from bodhan_eval.checks.translation import check_numbers
from bodhan_eval.languages import load_languages
from bodhan_eval.params import Params

L = load_languages()
P = Params.defaults()


@dataclass
class _R:
    text: str
    truncated: bool = False
    cached: bool = False


class FakeTranslator:
    """Back-translates by lookup; records calls."""

    def __init__(self, table: dict[str, str], truncated: bool = False):
        self.table, self.truncated, self.calls = table, truncated, []

    async def translate(self, text, target_language, script="native"):
        self.calls.append((text, target_language))
        if text not in self.table and "\n" in text:  # batched: one sentence per line
            return _R("\n".join(self.table[t] for t in text.split("\n")), self.truncated)
        return _R(self.table[text], self.truncated)


def run_bt(source, translation, table, **kw):
    fake = FakeTranslator(table, **kw)
    res = asyncio.run(check_backtranslation(fake, source, translation, L["hi"], P))
    return res, fake


def cats(res):
    return sorted(f.category for f in res.flags)


SRC = (
    "The Indian Space Research Organisation launched Chandrayaan-3 in July 2023. "
    "The lander touched down near the lunar south pole. "
    "It carried a small rover called Pragyan."
)
T1, T2, T3 = (
    "भारतीय अंतरिक्ष अनुसंधान संगठन ने जुलाई 2023 में चंद्रयान-3 लॉन्च किया।",
    "लैंडर चंद्रमा के दक्षिणी ध्रुव के पास उतरा।",
    "इसमें प्रज्ञान नाम का एक छोटा रोवर था।",
)
BT = {
    T1: "Indian Space Research Organisation launched Chandrayaan-3 in July 2023.",
    T2: "The lander landed near the south pole of the moon.",
    T3: "It had a small rover named Pragyan.",
}


# --- alignment ------------------------------------------------------------------------


def test_align_segments_one_to_one_and_gap():
    a = ["the cat sat on the mat", "dogs bark loudly at night", "birds sing in the morning"]
    b = ["the cat sat on a mat", "birds sing every morning"]
    pairs = align_segments(a, b, similarity, min_sim=30)
    assert [(p.a, p.b) for p in pairs] == [((0,), (0,)), ((1,), ()), ((2,), (1,))]


def test_align_segments_merge():
    a = ["The river is long and it flows through three countries before reaching the sea."]
    b = ["The river is long.", "It flows through three countries before reaching the sea."]
    pairs = align_segments(a, b, similarity, min_sim=30)
    assert [(p.a, p.b) for p in pairs] == [((0,), (0, 1))]


# --- reference --------------------------------------------------------------------------


def test_reference_scores_and_low_sentence():
    ref = f"{T1} {T2}"
    good = check_reference(f"{T1} {T2}", ref, P)
    assert good.ran and good.flags == [] and good.signals["chrf"] > 95
    bad = check_reference(f"{T1} {T3}", ref, P)
    assert bad.signals["chrf"] < good.signals["chrf"]
    assert bad.signals["low_sentences"]  # the wrong second sentence scores low
    low_span = bad.signals["low_sentences"][0]
    assert f"{T1} {T3}"[low_span[0] : low_span[1]] == T3


def test_reference_skipped_without_reference():
    res = check_reference(T1, None, P)
    assert not res.ran and res.skipped_reason == "no reference"


def test_reference_ignores_digit_system():
    assert check_reference("मूल्य १२५ है।", "मूल्य 125 है।", P).signals["chrf"] == 100.0


# --- back-translation ------------------------------------------------------------------


def test_bt_clean_item_has_no_flags():
    res, fake = run_bt(SRC, f"{T1} {T2} {T3}", BT)
    assert res.flags == [], [f.evidence for f in res.flags]
    assert len(fake.calls) == 1 and fake.calls[0][1] == "en"  # one batched request
    assert res.signals["bt_mode"] == "batched"
    assert res.signals["source_numbers_missing_in_bt"] == []
    assert res.signals["bt_chrf"] > 50


def test_bt_sentence_omission_spans_neighbour():
    tr = f"{T1} {T3}"
    res, _ = run_bt(SRC, tr, BT)
    assert cats(res) == ["omission"]
    f = res.flags[0]
    assert SRC[f.source_span[0] : f.source_span[1]].startswith("The lander")
    assert f.severity == "major" and f.confidence >= 0.5
    assert tr[f.span[0] : f.span[1]] in (T1, T3)


def test_bt_sentence_addition():
    extra = "इस मिशन की लागत दस हज़ार करोड़ रुपये थी।"
    tr = f"{T1} {T2} {extra} {T3}"
    res, _ = run_bt(SRC, tr, {**BT, extra: "This mission cost ten thousand crore rupees."})
    assert cats(res) == ["addition"]
    assert tr[res.flags[0].span[0] : res.flags[0].span[1]] == extra


def test_bt_replaced_sentence_is_mistranslation():
    wrong = "लैंडर मंगल ग्रह पर दुर्घटनाग्रस्त हो गया।"
    tr = f"{T1} {wrong} {T3}"
    res, _ = run_bt(SRC, tr, {**BT, wrong: "Crashed on planet Mars, completely destroyed."})
    assert cats(res) == ["mistranslation"]
    f = res.flags[0]
    assert tr[f.span[0] : f.span[1]] == wrong
    assert SRC[f.source_span[0] : f.source_span[1]].startswith("The lander")


def test_bt_partial_omission_inside_sentence():
    src = (
        "The committee approved the new budget after a long debate, "
        "despite strong objections from several opposition members and local farmers."
    )
    tr = "समिति ने लंबी बहस के बाद नया बजट मंजूर किया।"
    res, _ = run_bt(src, tr, {tr: "The committee approved the new budget after a long debate."})
    assert cats(res) == ["omission"]
    f = res.flags[0]
    assert f.severity == "minor"
    assert "objections" in src[f.source_span[0] : f.source_span[1]]


def test_bt_missing_entity_is_term_candidate():
    tr = f"{T1} {T2} {T3}"
    res, _ = run_bt(SRC, tr, {**BT, T3: "It had a small rover named Vikram."})
    assert cats(res) == ["term"]
    assert res.flags[0].details["entity"] == "Pragyan"
    assert tr[res.flags[0].span[0] : res.flags[0].span[1]] == T3


def test_bt_acronym_expansion_is_not_a_term_error():
    src = "ISRO built the rocket."
    tr = "इसरो ने रॉकेट बनाया।"
    res, _ = run_bt(src, tr, {tr: "Indian Space Research Organisation built the rocket."})
    assert "term" not in cats(res)


def test_bt_truncation_halves_confidence():
    tr = f"{T1} {T3}"
    full, _ = run_bt(SRC, tr, BT)
    trunc, _ = run_bt(SRC, tr, BT, truncated=True)
    assert trunc.signals["bt_truncated"]
    assert trunc.flags[0].confidence == round(full.flags[0].confidence * 0.5, 3)


def test_bt_skips_segments_without_letters():
    src = "| Year | Value |\n|---|---|\n| 2023 | 5 |"
    tr = "| वर्ष | मूल्य |\n|---|---|\n| 2023 | 5 |"
    rows = tr.split("\n")
    res, fake = run_bt(src, tr, {rows[0]: "| Year | Value |", rows[2]: "| 2023 | 5 |"})
    assert [t for t, _ in fake.calls] == [rows[0]]  # rule row and digit-only row not sent
    assert res.flags == []


def test_reconcile_numbers_spelled_out_vs_missing():
    src = "The bridge is 120 metres long and was built in 1998."
    tr = "पुल एक सौ बीस मीटर लंबा है और बनाया गया था।"  # 120 in words, 1998 dropped
    nums = check_numbers(src, tr, L["hi"], P)
    before = {f.details["expected"]: f.confidence for f in nums.flags}
    assert set(before) == {"120", "1998"}
    bt, _ = run_bt(src, tr, {tr: "The bridge is 120 metres long and was built."})
    reconcile_numbers(nums, bt)
    after = {f.details["expected"]: f.confidence for f in nums.flags}
    assert after["120"] < before["120"]
    assert after["1998"] > before["1998"]


def test_reconcile_numbers_indian_scale_rewrite():
    """'400 million' -> '40 crore' is correct; the BT shows it, so confidence must go down."""
    src = "More than 400 million people live in its basin."
    tr = "இதன் வடிநிலத்தில் 40 கோடிக்கும் அதிகமான மக்கள் வாழ்கின்றனர்."
    nums = check_numbers(src, tr, L["ta"], P)
    before = nums.flags[0].confidence
    bt, _ = run_bt(src, tr, {tr: "More than 40 crore people live in its basin."})
    assert bt.signals["source_numbers_in_bt"] == ["400"]
    reconcile_numbers(nums, bt)
    assert nums.flags[0].confidence < before and nums.flags[0].confidence < 0.5


class MergingTranslator(FakeTranslator):
    """Returns the batched request as one line (line count mismatch)."""

    async def translate(self, text, target_language, script="native"):
        if "\n" in text:
            self.calls.append((text, target_language))
            return _R(" ".join(self.table[t] for t in text.split("\n")))
        return await super().translate(text, target_language, script)


def test_bt_falls_back_to_per_sentence_when_lines_merge():
    fake = MergingTranslator(BT)
    res = asyncio.run(check_backtranslation(fake, SRC, f"{T1} {T2} {T3}", L["hi"], P))
    assert res.signals["bt_mode"] == "per_sentence" and res.flags == []
    assert len(fake.calls) == 4  # 1 batched attempt + 3 single sentences


class CachedTranslator(FakeTranslator):
    def cached_translate(self, text, target_language, script="native"):
        return _R(self.table[text]) if text in self.table else None


def test_bt_uses_per_sentence_cache_without_calls():
    fake = CachedTranslator(BT)
    res = asyncio.run(check_backtranslation(fake, SRC, f"{T1} {T2} {T3}", L["hi"], P))
    assert res.signals["bt_mode"] == "cached" and fake.calls == []


def test_bt_sentence_split_is_not_an_addition():
    src = "The Olympics have not brought lasting peace to the world, even during the Games."
    t1, t2 = "ओलंपिक ने दुनिया में स्थायी शांति नहीं लाई है।", "खेलों के दौरान भी नहीं।"
    table = {t1: "The Olympics have not brought lasting peace to the world.",
             t2: "Not even during the games."}  # fmt: skip
    res, _ = run_bt(src, f"{t1} {t2}", table)
    assert "addition" not in cats(res)
