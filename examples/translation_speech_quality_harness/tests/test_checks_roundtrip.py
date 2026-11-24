"""Round-trip checks against a fake translator that returns the real day-1 outputs for known inputs."""

from conftest import fixture_rows, item, make_clients

from bodhan_eval.checks_roundtrip import _bt_candidates, agreement, back_translation, coverage, regen_candidates, regenerate

ROWS = fixture_rows()
P1 = ROWS["t02"]["source"]
P1_HI = ROWS["t02"]["translation"]
DANDA = "।"


def hi_sentences():
    return [s.strip() + DANDA for s in P1_HI.split(DANDA) if s.strip()]


def src_sentence(n):
    return [s.strip() + "." for s in P1.split(".") if s.strip()][n]


# ---------------------------------------------------------------- back-translation

def test_paraphrased_back_translation_raises_nothing():
    bt = ("The heart pumps blood throughout the body. Arteries carry blood away from the heart while veins bring it back. "
          "An adult heart beats about 72 times per minute.")
    assert _bt_candidates(P1, P1_HI, bt) == []


def test_missing_sentence_in_back_translation_is_an_omission_suspect():
    bt = "The heart pumps blood through the body. An adult heart beats about 72 times per minute."
    c = _bt_candidates(P1, P1_HI, bt)
    assert len(c) == 1 and c[0]["category"] == "omission" and c[0]["class"] == "noisy"
    s, e = c[0]["source_span"]
    assert P1[s:e] == src_sentence(1)


def test_extra_sentence_in_back_translation_is_an_addition_suspect():
    tr = P1_HI + " " + ROWS["t01"]["translation"]
    bt = P1 + " Water boils at 100 degrees Celsius at sea level."
    c = _bt_candidates(P1, tr, bt)
    assert [x["category"] for x in c] == ["addition"]
    s, e = c[0]["translation_span"]
    assert tr[s:e] == ROWS["t01"]["translation"]


def test_recheck_drops_omission_when_sentence_wise_back_translation_has_it(tmp_path, fake_bodhan, fake_llm):
    bodhan, _ = make_clients(tmp_path, fake_bodhan, fake_llm)
    english = dict(zip(hi_sentences(), [src_sentence(i) for i in range(3)], strict=True))

    def translate(text, lang):
        if text == P1_HI:
            return src_sentence(0) + " " + src_sentence(2)  # the full back-translation lost the middle sentence
        return english.get(text, text)

    fake_bodhan.translate = translate
    out = back_translation(item(P1, P1_HI), bodhan)
    assert out["candidates"] == []
    assert len(fake_bodhan.requests) == 4  # one full back-translation, then three sentences


def test_recheck_keeps_real_omission(tmp_path, fake_bodhan, fake_llm):
    bodhan, _ = make_clients(tmp_path, fake_bodhan, fake_llm)
    sents = hi_sentences()
    tr = sents[0] + " " + sents[2]
    english = {sents[0]: src_sentence(0), sents[2]: src_sentence(2), tr: src_sentence(0) + " " + src_sentence(2)}
    fake_bodhan.translate = lambda text, lang: english.get(text, text)
    c = back_translation(item(P1, tr), bodhan)["candidates"]
    assert len(c) == 1 and c[0]["evidence"]["recheck_coverage"] < 0.4


def test_coverage_tolerates_inflection():
    assert coverage(["pumps", "arteries"], ["pump", "artery"]) == 1.0


# ---------------------------------------------------------------- regeneration diff

def regen(tmp_path, fake_bodhan, fake_llm, supplied, rid="t02"):
    bodhan, _ = make_clients(tmp_path, fake_bodhan, fake_llm)
    it = item(ROWS[rid]["source"], supplied, ROWS[rid]["lang"])
    return it, regenerate(it, bodhan)


def test_identical_supplied_translation_has_no_regions(tmp_path, fake_bodhan, fake_llm):
    it, rg = regen(tmp_path, fake_bodhan, fake_llm, P1_HI)
    assert rg["regions"] == [] and rg["similarity"] == 1.0 and regen_candidates(it, rg, 0.9) == []


def test_deleted_sentence_is_omission_located_in_source(tmp_path, fake_bodhan, fake_llm):
    sents = hi_sentences()
    it, rg = regen(tmp_path, fake_bodhan, fake_llm, sents[0] + " " + sents[2])
    c = regen_candidates(it, rg, 0.9)
    assert [x["category"] for x in c] == ["omission"] and c[0]["class"] == "exact"
    s, e = c[0]["source_span"]
    assert P1[s:e] == src_sentence(1)


def test_changed_digit_is_number(tmp_path, fake_bodhan, fake_llm):
    supplied = P1_HI.replace("७२", "८२")  # Devanagari 72 -> 82
    it, rg = regen(tmp_path, fake_bodhan, fake_llm, supplied)
    c = regen_candidates(it, rg, 0.9)
    assert [x["category"] for x in c] == ["number"]
    s, e = c[0]["translation_span"]
    assert "८२" in supplied[s:e]


def test_inserted_sentence_is_addition_with_exact_span(tmp_path, fake_bodhan, fake_llm):
    extra = ROWS["t01"]["translation"]
    supplied = P1_HI + " " + extra
    it, rg = regen(tmp_path, fake_bodhan, fake_llm, supplied)
    c = regen_candidates(it, rg, 0.9)
    assert [x["category"] for x in c] == ["addition"]
    s, e = c[0]["translation_span"]
    assert supplied[s:e] == extra


def test_swapped_word_is_mistranslation(tmp_path, fake_bodhan, fake_llm):
    words = P1_HI.split()
    supplied = P1_HI.replace(words[1], ROWS["t01"]["translation"].split()[0], 1)
    it, rg = regen(tmp_path, fake_bodhan, fake_llm, supplied)
    c = regen_candidates(it, rg, 0.9)
    assert [x["category"] for x in c] == ["mistranslation"]


def test_low_agreement_downgrades_to_noisy(tmp_path, fake_bodhan, fake_llm):
    sents = hi_sentences()
    it, rg = regen(tmp_path, fake_bodhan, fake_llm, sents[0] + " " + sents[2])
    assert {x["class"] for x in regen_candidates(it, rg, 0.3)} == {"noisy"}
    assert {x["class"] for x in regen_candidates(it, rg, None)} == {"noisy"}


def test_completely_different_text_is_one_whole_item_candidate(tmp_path, fake_bodhan, fake_llm):
    it, rg = regen(tmp_path, fake_bodhan, fake_llm, ROWS["t01"]["translation"])
    c = regen_candidates(it, rg, 0.9)
    assert len(c) == 1 and c[0]["translation_span"] == [0, len(ROWS["t01"]["translation"])]


def test_agreement_needs_enough_items():
    same, diff = {"regions": []}, {"regions": [1]}
    assert agreement([same] * 5) is None
    assert agreement([same] * 8 + [diff, diff]) == 0.8
