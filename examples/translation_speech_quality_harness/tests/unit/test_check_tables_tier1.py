"""Must-catch / must-not-flag tables for the tier-1 checks ``back_translation`` and ``entities`` (TESTING.md §2).

Both run against the stub server: back-translations are keyed by the exact segment the check sends,
transliterations by term. Tier-1 evidence is noisy by design (ARCHITECTURE.md §3), so the bar is: a
must-catch row emits a correctly located signal strong enough for the review queue (>= 0.3; whole
missing/extra sentences must flag outright), and a must-not-flag row emits nothing that strong.
"""

from __future__ import annotations

import pytest

from bodhan_eval.checks.roundtrip import BackTranslationCheck, EntityCheck, source_entities

REVIEW = 0.3  # lowest score that reaches the review queue under the default thresholds


def strong(r, category: str) -> list:
    return [s for s in r.signals if s.category == category and s.score >= REVIEW]


# ---------------------------------------------------------------------------
# back-translation
# ---------------------------------------------------------------------------

BT_CATCH = [
    pytest.param(
        "Water boils at 100 degrees. Ice melts quickly in summer. Steam is very hot.",
        "पानी 100 डिग्री पर उबलता है। भाप बहुत गर्म होती है।",
        {"पानी 100 डिग्री पर उबलता है।": "Water boils at 100 degrees.", "भाप बहुत गर्म होती है।": "Steam is very hot."},
        "omission", "Ice melts quickly in summer.", 0.5, id="whole-sentence-missing"),
    pytest.param(
        "Plants make food using sunlight, and they release oxygen into the air.",
        "पौधे सूर्य के प्रकाश का उपयोग करके भोजन बनाते हैं।",
        {"पौधे सूर्य के प्रकाश का उपयोग करके भोजन बनाते हैं।": "Plants make food using sunlight."},
        "omission", "they release oxygen into the air.", REVIEW, id="clause-missing"),
    pytest.param(
        "Water boils at 100 degrees.",
        "पानी 100 डिग्री पर उबलता है। चाँद हर महीने पृथ्वी की परिक्रमा करता है।",
        {"पानी 100 डिग्री पर उबलता है।": "Water boils at 100 degrees.",
         "चाँद हर महीने पृथ्वी की परिक्रमा करता है।": "The moon orbits the earth every month."},
        "addition", None, 0.5, id="extra-sentence"),
    pytest.param(
        "The doctor advised the patient to rest for two weeks.",
        "शिक्षक ने छात्र को दो घंटे पढ़ने के लिए कहा।",
        {"शिक्षक ने छात्र को दो घंटे पढ़ने के लिए कहा।": "The teacher told the student to study for two hours."},
        "mistranslation", None, REVIEW, id="meaning-drift"),
]  # fmt: skip

BT_CLEAN = [
    pytest.param("The children were very happy after the match.", "मैच के बाद बच्चे बहुत खुश थे।",
                 {"मैच के बाद बच्चे बहुत खुश थे।": "After the game, the children were very happy."}, id="paraphrase"),
    pytest.param("Water boils at 100 degrees. It then turns into steam.",
                 "पानी 100 डिग्री पर उबलता है और फिर भाप में बदल जाता है।",
                 {"पानी 100 डिग्री पर उबलता है और फिर भाप में बदल जाता है।": "Water boils at 100 degrees and then turns into steam."},
                 id="two-sentences-merged"),
    pytest.param("Water boils at 100 degrees and then turns into steam.",
                 "पानी 100 डिग्री पर उबलता है। फिर यह भाप में बदल जाता है।",
                 {"पानी 100 डिग्री पर उबलता है।": "Water boils at 100 degrees.", "फिर यह भाप में बदल जाता है।": "Then it turns into steam."},
                 id="sentence-split"),
    pytest.param("Ice melts in spring. Rivers flood in summer.",
                 "गर्मियों में नदियों में बाढ़ आती है। वसंत में बर्फ पिघलती है।",
                 {"गर्मियों में नदियों में बाढ़ आती है।": "Rivers flood in summer.", "वसंत में बर्फ पिघलती है।": "Ice melts in spring."},
                 id="reordered-sentences"),
    pytest.param("The scientist measured the temperature of the liquid carefully.",
                 "वैज्ञानिक ने द्रव का तापमान सावधानी से मापा।",
                 {"वैज्ञानिक ने द्रव का तापमान सावधानी से मापा।": "The researcher carefully measured the temperature of the fluid."},
                 id="synonyms"),
]  # fmt: skip


@pytest.mark.parametrize(("source", "translation", "bt", "category", "source_text", "min_score"), BT_CATCH)
def test_back_translation_must_catch(
    start_stub, make_ctx, run_one, source, translation, bt, category, source_text, min_score
) -> None:
    r = run_one(BackTranslationCheck, make_ctx(start_stub({"translate": {"en": bt}})), source, translation)
    hits = [s for s in strong(r, category) if s.score >= min_score]
    assert hits, [(s.category, s.score, s.evidence) for s in r.signals]
    if source_text:
        assert any(s.source_span and source_text in source[s.source_span[0] : s.source_span[1]] for s in hits)


@pytest.mark.parametrize(("source", "translation", "bt"), BT_CLEAN)
def test_back_translation_must_not_flag(start_stub, make_ctx, run_one, source, translation, bt) -> None:
    r = run_one(BackTranslationCheck, make_ctx(start_stub({"translate": {"en": bt}})), source, translation)
    assert r.checks["back_translation"]["status"] == "ok"
    assert [s for s in r.signals if s.score >= REVIEW] == [], [(s.category, s.score, s.evidence) for s in r.signals]


# ---------------------------------------------------------------------------
# entities and terms
# ---------------------------------------------------------------------------

ENTITY_CATCH = [
    pytest.param("The theory of relativity was proposed by Albert Einstein.",
                 "सापेक्षता का सिद्धांत आइज़क न्यूटन ने प्रस्तावित किया था।",
                 {"Albert Einstein": "अल्बर्ट आइंस्टीन"}, "The theory of relativity was proposed by Isaac Newton.",
                 id="name-replaced-by-another-name"),
    pytest.param("The element radium was discovered by Marie Curie.",
                 "रेडियम तत्व की खोज मारकी क्यूरोज़ ने की थी।",
                 {"Marie Curie": "मैरी क्यूरी"}, "The element radium was discovered by Marki Kyuroz.",
                 id="garbled-transliteration"),
]  # fmt: skip

ENTITY_CLEAN = [
    pytest.param("The laws of motion were given by Isaac Newton.", "गति के नियम Isaac Newton ने दिए थे।",
                 {}, "The laws of motion were given by Isaac Newton.", id="name-kept-in-latin"),
    pytest.param("The laws of motion were given by Isaac Newton.", "गति के नियम आइज़क न्यूटन ने दिए थे।",
                 {"Isaac Newton": "आइज़क न्यूटन"}, "The laws of motion were given by him.", id="correct-transliteration"),
    pytest.param("The laws of motion were given by Isaac Newton.", "गति के नियम आइज़क न्यूटन ने दिए थे।",
                 {"Isaac Newton": "आइजैक न्यूटन"}, "The laws of motion were given by him.", id="transliteration-spelling-variant"),
    pytest.param("Our solar system is part of the Milky Way.", "हमारा सौर मंडल आकाशगंगा का हिस्सा है।",
                 {"Milky Way": "मिल्की वे"}, "Our solar system is part of the Milky Way.", id="translated-by-meaning-confirmed-by-bt"),
]  # fmt: skip


ENTITY_EXTRACTION = [  # (source, names that must be found, words that must not be taken for names)
    pytest.param("Near Earth's surface, gravity is 9.8 m/s². Earth is round.", {"Earth"}, {"Near Earth", "Near"},
                 id="sentence-initial-preposition-is-not-part-of-the-name"),
    pytest.param("The Green Revolution began in Punjab.", {"Green Revolution", "Punjab"}, {"The Green Revolution"},
                 id="sentence-initial-article-is-not-part-of-the-name"),
    pytest.param("During Mughal rule, Agra grew.", {"Mughal", "Agra"}, {"During Mughal"},
                 id="sentence-initial-during"),
    pytest.param("Albert Einstein proposed relativity in Berlin.", {"Albert Einstein", "Berlin"}, {"Einstein"},
                 id="a-full-name-at-the-start-stays-whole"),
    pytest.param("| River unit | Draining into | Catchment area |\n|---|---|---|\n| Ganga | Bay of Bengal | 26.3 |",
                 {"Bengal"}, {"River", "Draining", "Catchment"}, id="table-cells-start-like-sentences"),
    pytest.param("Consumers have the right to:\n\n- Seek redress against unfair trade.\n- Seek consumer education.",
                 set(), {"Seek"}, id="a-word-capitalised-only-at-line-starts-is-not-a-name"),
    pytest.param("Consumers have the right to:\n\n- Be protected against hazards.\n- Be informed of prices.",
                 set(), {"Be"}, id="function-word-at-line-starts"),
    pytest.param("Sectors:\n- **Tertiary**: services.\n- **Primary**: raw materials.", set(), {"Tertiary", "Primary"},
                 id="bold-list-item-starts-like-a-sentence"),
    pytest.param("Water from the Ganga reaches Kolkata. Ganga is sacred.", {"Ganga", "Kolkata"}, set(),
                 id="a-name-also-capitalised-mid-sentence-stays"),
]  # fmt: skip


@pytest.mark.parametrize(("source", "names", "not_names"), ENTITY_EXTRACTION)
def test_entity_extraction(source: str, names: set[str], not_names: set[str]) -> None:
    found = [w for w, _, _ in source_entities(source)]
    assert names <= set(found) and not (not_names & set(found)), found
    for w, a, b in source_entities(source):
        assert source[a:b] == w  # offsets still point at the (trimmed) name


def entity_ctx(start_stub, make_ctx, translation: str, translit: dict, bt: str):
    return make_ctx(start_stub({"translate": {"en": {translation: bt}}, "transliterate": {"hi": translit}}))


@pytest.mark.parametrize(("source", "translation", "translit", "bt"), ENTITY_CATCH)
def test_entities_must_catch(start_stub, make_ctx, run_chain, source, translation, translit, bt) -> None:
    ctx = entity_ctx(start_stub, make_ctx, translation, translit, bt)
    r = run_chain([BackTranslationCheck, EntityCheck], ctx, source, translation)
    hits = strong(r, "term")
    assert hits, [(s.category, s.score, s.evidence) for s in r.signals]
    name = next(iter(translit))
    assert any(s.source_span and source[s.source_span[0] : s.source_span[1]] == name for s in hits)


@pytest.mark.parametrize(("source", "translation", "translit", "bt"), ENTITY_CLEAN)
def test_entities_must_not_flag(start_stub, make_ctx, run_chain, source, translation, translit, bt) -> None:
    ctx = entity_ctx(start_stub, make_ctx, translation, translit, bt)
    r = run_chain([BackTranslationCheck, EntityCheck], ctx, source, translation)
    assert r.checks["entities"]["status"] == "ok"
    assert strong(r, "term") == [], [(s.category, s.score, s.evidence) for s in r.signals]


def test_a_missing_name_alone_is_a_hint_for_review_not_a_flag(start_stub, make_ctx, run_chain, fuse_item) -> None:
    # live false alarms: names are often translated or written as exonyms (BCE -> கி.மு, Satluj -> শতদ্রু), D46
    source, translation, translit, bt = ENTITY_CATCH[0].values
    ctx = entity_ctx(start_stub, make_ctx, translation, translit, bt)
    r = run_chain([BackTranslationCheck, EntityCheck], ctx, source, translation)
    flags, uncertain = fuse_item(r)
    assert not [f for f in flags if f.category == "term"] and [f for f in uncertain if f.category == "term"]
