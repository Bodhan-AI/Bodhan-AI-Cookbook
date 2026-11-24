"""Translation checks and recovery: script, numbers, terms, judging, candidate choice, isolation."""

from bodhan_dub import textcheck
from bodhan_dub.bodhan import BodhanError, Translation
from bodhan_dub.glossary import Term
from bodhan_dub.llm import LLMError
from bodhan_dub.repair import Unit
from bodhan_dub.translate import Candidate, Translator


def unit(uid, text, dropped=None):
    return Unit(id=uid, start=0.0, end=5.0, text=text, raw_text=text, source_ids=["s0000"], dropped=dropped)


# ---------------------------------------------------------------------------
# textcheck
# ---------------------------------------------------------------------------

def test_script_share_per_language():
    assert textcheck.script_share("कोण AOC 120 डिग्री है", "hi") > 0.7
    assert textcheck.script_share("கோணம் AOC", "ta") > 0.6
    assert textcheck.script_share("Angle AOC is 120 degrees", "hi") == 0.0
    assert textcheck.script_share("कोण 120", "ta") == 0.0  # Devanagari is wrong for Tamil


def test_native_digits_are_normalised_before_comparing_numbers():
    assert textcheck.ascii_digits("১৮০ और २५ and ௧௨") == "180 और 25 and 12"
    assert textcheck.missing_numbers("360 minus 120 is 240", "৩৬০ থেকে ১২০ বিয়োগ করলে ২৪০") == []
    assert textcheck.missing_numbers("70 plus 80 is 150, and 80", "70 जमा 80 = 150") == ["80"]
    assert textcheck.missing_numbers("It costs 1,000 rupees", "1000 रुपये") == []


def test_terms_match_plural_hyphen_and_spaced_labels_but_not_other_concepts():
    assert textcheck.term_in("reflex angle", "technical", "a reflex angles question")
    assert not textcheck.term_in("reflex angle", "technical", "an angle of reflection")
    assert textcheck.term_in("non-common arms", "technical", "their non common arm")
    assert textcheck.term_in("AOC", "label", "angle A O C") and textcheck.term_in("AOC", "label", "A-O-C")
    assert not textcheck.term_in("OA", "label", "an oak")


def test_single_letter_labels_are_not_checked():
    terms = [("A", "label"), ("AOC", "label"), ("ray", "technical")]
    assert textcheck.checkable_terms(terms, "A ray from A to AOC") == [("AOC", "label"), ("ray", "technical")]


def test_split_sentences_handles_question_marks():
    assert textcheck.split_sentences("Are they adjacent? Yes. Write it!") == ["Are they adjacent?", "Yes.", "Write it!"]


# ---------------------------------------------------------------------------
# translator, with a scripted Bodhan client and LLM
# ---------------------------------------------------------------------------

class FakeClient:
    """translate(text, lang, script) answers from a table; back-translation looks up the Indic text."""

    def __init__(self, forward, back, fail_langs=()):
        self.forward, self.back, self.fail_langs = forward, back, set(fail_langs)
        self.calls = []

    def translate(self, text, target_language, script="native", bill_to=None):
        self.calls.append((text, target_language, script))
        if target_language in self.fail_langs:
            raise BodhanError(503, "internal_error", "down")
        if target_language == "en":
            return Translation(self.back[text], False, 1)
        return Translation(self.forward[(text, target_language, script)], False, 1)


class FakeJudge:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def complete_json(self, system, user):
        self.calls += 1
        reply = self.replies.pop(0) if self.replies else {"verdicts": []}
        if isinstance(reply, Exception):
            raise reply
        return reply


SRC = "A reflex angle is more than 180 degrees. Write it."
GLOSSARY = [Term("reflex angle", "technical")]


def test_clean_translation_is_accepted_without_alternatives():
    good = "प्रतिवर्ती कोण 180 डिग्री से अधिक होता है। इसे लिखिए।"
    client = FakeClient({(SRC, "hi", "native"): good}, {good: SRC})
    judge = FakeJudge({"verdicts": [{"id": "u0000:unit", "verdict": "ok", "issue": ""}]})
    result = Translator(client, judge, GLOSSARY).translate_language([unit("u0000", SRC)], "hi")
    out = result.units[0]
    assert (out.status, out.chosen, len(out.candidates)) == ("ok", "unit", 1)
    assert judge.calls == 1


def test_mistranslated_term_triggers_alternatives_and_the_best_one_wins():
    bad = "परावर्तन कोण 180 डिग्री से अधिक होता है। इसे लिखिए।"
    s1, s2 = "परावर्तन कोण 180 डिग्री से अधिक होता है।", "इसे लिखिए।"
    mixed = "reflex angle 180 degrees से ज़्यादा होता है। इसे लिखो।"
    client = FakeClient(
        {(SRC, "hi", "native"): bad,
         ("A reflex angle is more than 180 degrees.", "hi", "native"): s1, ("Write it.", "hi", "native"): s2,
         (SRC, "hi", "codemix"): mixed},
        {bad: "The angle of reflection is more than 180 degrees. Write it.",
         f"{s1} {s2}": "The angle of reflection is more than 180 degrees. Write it.",
         mixed: "A reflex angle is more than 180 degrees. Write it."})
    judge = FakeJudge({"verdicts": [{"id": "u0000:unit", "verdict": "major", "issue": "wrong term"}]},
                      {"verdicts": [{"id": "u0000:codemix", "verdict": "ok"}]})
    out = Translator(client, judge, GLOSSARY).translate_language([unit("u0000", SRC)], "hi").units[0]
    assert [c.kind for c in out.candidates] == ["unit", "sentences", "codemix"]
    assert out.candidates[0].missing_terms == ["reflex angle"]
    assert (out.chosen, out.status, out.translation) == ("codemix", "ok", mixed)


def test_judge_outage_falls_back_to_deterministic_checks():
    good = "प्रतिवर्ती कोण 180 डिग्री से अधिक होता है। इसे लिखिए।"
    client = FakeClient({(SRC, "hi", "native"): good}, {good: SRC})
    result = Translator(client, FakeJudge(LLMError("down")), GLOSSARY).translate_language([unit("u0000", SRC)], "hi")
    assert result.judge_failed == 1 and result.units[0].status == "ok"


def test_wrong_script_is_a_hard_failure_and_the_unit_is_skipped_if_nothing_better():
    src = "Write it."
    client = FakeClient({(src, "hi", "native"): "Write it.", (src, "hi", "codemix"): "Write it."},
                        {"Write it.": "Write it."})
    out = Translator(client, FakeJudge(), []).translate_language([unit("u0000", src)], "hi").units[0]
    assert out.status == "skipped" and "wrong script" in out.note and out.translation == ""


def test_dropped_units_are_skipped_without_any_call():
    client = FakeClient({}, {})
    out = Translator(client, FakeJudge(), []).translate_language([unit("u0000", "", dropped="music")], "hi").units[0]
    assert out.status == "skipped" and client.calls == []


def test_candidate_ranking_prefers_usable_then_fewer_problems_then_verdict():
    usable_major = Candidate("unit", text="x", verdict="major")
    usable_ok = Candidate("codemix", text="y", verdict="ok")
    broken = Candidate("sentences", hard_fail=["empty translation"], verdict="ok")
    missing_term = Candidate("sentences", text="z", missing_terms=["ray"], verdict="ok")
    assert max([usable_major, usable_ok, broken, missing_term], key=Candidate.rank) is usable_ok
    assert max([broken, missing_term], key=Candidate.rank) is missing_term
    # the judge outranks a literal term miss; on a full tie the native translation wins
    paraphrased = Candidate("unit", text="a", missing_terms=["common arm"], verdict="minor")
    assert max([paraphrased, Candidate("codemix", text="b", verdict="minor")], key=Candidate.rank).kind == "codemix"
    assert max([Candidate("unit", text="a", verdict="ok"), Candidate("codemix", text="b", verdict="ok")],
               key=Candidate.rank).kind == "unit"


def test_term_paraphrase_judged_minor_does_not_trigger_alternatives():
    src = "They share a common arm."
    native = "उनकी एक उभयनिष्ठ भुजा है।"
    client = FakeClient({(src, "hi", "native"): native}, {native: "They have a common side."})
    judge = FakeJudge({"verdicts": [{"id": "u0000:unit", "verdict": "minor", "issue": "arm -> side, same meaning"}]})
    out = Translator(client, judge, [Term("common arm", "technical")]).translate_language([unit("u0000", src)],
                                                                                          "hi").units[0]
    assert (out.status, out.chosen, len(out.candidates)) == ("ok", "unit", 1)
    assert "paraphrased" in out.note


def test_failing_api_marks_units_skipped_not_crashing():
    client = FakeClient({}, {}, fail_langs={"ta"})
    out = Translator(client, FakeJudge(), []).translate_language([unit("u0000", "Hello.")], "ta").units[0]
    assert out.status == "skipped" and "translation call failed" in out.note


def test_keep_terms_must_survive_untranslated_and_codemix_can_rescue_them():
    src = "Welcome to Bodhan."
    native, mixed = "बोधन में आपका स्वागत है।", "Bodhan में आपका स्वागत है।"
    client = FakeClient({(src, "hi", "native"): native, (src, "hi", "codemix"): mixed},
                        {native: "Welcome to Bodhan.", mixed: "Welcome to Bodhan."})
    glossary = [Term("Bodhan", "name", keep=True, source="user")]
    out = Translator(client, FakeJudge(), glossary).translate_language([unit("u0000", src)], "hi").units[0]
    assert out.candidates[0].missing_keep == ["Bodhan"]
    assert (out.chosen, out.status) == ("codemix", "ok")


def test_judge_is_called_in_batches():
    units = [unit(f"u{i:04d}", f"Sentence {i}.") for i in range(32)]
    forward = {(u.text, "hi", "native"): f"वाक्य {i}।" for i, u in enumerate(units)}
    back = {f"वाक्य {i}।": f"Sentence {i}." for i in range(32)}
    judge = FakeJudge()
    Translator(FakeClient(forward, back), judge, []).translate_language(units, "hi")
    assert judge.calls == 3  # 15 + 15 + 2


def test_native_digits_for_speech_only_touch_real_numbers():
    nd = textcheck.native_digits
    assert nd("कोण 180 डिग्री, 3.5 और 12,000", "hi") == "कोण १८० डिग्री, ३.५ और १२,०००"
    assert nd("கோணம் 90 டிகிரி", "ta") == "கோணம் ௯௦ டிகிரி"
    assert nd("زاویہ 180 ڈگری", "ur") == "زاویہ ۱۸۰ ڈگری"
    assert nd("H2O और COVID-19 में 7 अणु", "hi") == "H2O और COVID-19 में ७ अणु"  # names keep ASCII digits
    assert nd("already ১৮০", "bn") == "already ১৮০" and nd("x 5", "en") == "x 5"  # no table entry: unchanged
    assert set(textcheck.NATIVE_ZERO) == set(__import__("bodhan_dub.languages").languages.TARGET_LANGS)


def test_a_term_is_rendered_one_way_across_units():
    a, b = "A linear pair adds to 180.", "Name a linear pair."
    a_native, a_mixed = "रैखिक युग्म का योग 180 है।", "linear pair का योग 180 है।"
    b_native, b_mixed = "एक रैखिक युग्म बताइए।", "एक linear pair बताइए।"
    client = FakeClient(
        {(a, "hi", "native"): a_native, (a, "hi", "codemix"): a_mixed,
         (b, "hi", "native"): b_native, (b, "hi", "codemix"): b_mixed},
        {a_native: "A linear sum adds to 180.", a_mixed: a, b_native: b, b_mixed: b})
    judge = FakeJudge({"verdicts": [{"id": "u0000:unit", "verdict": "major", "issue": "wrong term"},
                                    {"id": "u0001:unit", "verdict": "ok"}]},
                      {"verdicts": [{"id": "u0000:codemix", "verdict": "ok"}]},
                      {"verdicts": [{"id": "u0001:codemix", "verdict": "ok"}]})
    result = Translator(client, judge, [Term("linear pair", "technical")]).translate_language(
        [unit("u0000", a), unit("u0001", b)], "hi")
    assert [u.chosen for u in result.units] == ["codemix", "codemix"]  # u0001 switched to match u0000
    assert result.harmonised == ["u0001"]
    assert result.summary()["terms"]["still_mixed"] == []
    assert result.term_rendering["linear pair"] == {"english": ["u0000", "u0001"], "native": []}


def test_user_renderings_are_checked_and_reported_not_inserted():
    src = "A linear pair."
    out_text = "एक रैखिक जोड़ी।"
    client = FakeClient({(src, "hi", "native"): out_text}, {out_text: src})
    glossary = [Term("linear pair", "technical", source="user", renderings={"hi": "रैखिक युग्म"})]
    result = Translator(client, FakeJudge(), glossary).translate_language([unit("u0000", src)], "hi")
    assert result.units[0].translation == out_text  # never rewritten
    assert result.renderings_missing == {"linear pair": ["u0000"]}


def test_a_minority_english_rendering_does_not_cascade_into_more_code_mixing():
    a, b, c = "A linear pair adds to 180.", "Name a linear pair.", "Draw a linear pair."
    a_mixed = "linear pair का योग 180 है।"
    native = {b: "एक रैखिक युग्म बताइए।", c: "एक रैखिक युग्म बनाइए।"}
    client = FakeClient(
        {(a, "hi", "native"): "रैखिक जोड़ का योग 180 है।", (a, "hi", "codemix"): a_mixed,
         **{(k, "hi", "native"): v for k, v in native.items()}},
        {"रैखिक जोड़ का योग 180 है।": "A linear sum adds to 180.", a_mixed: a, native[b]: b, native[c]: c})
    judge = FakeJudge({"verdicts": [{"id": "u0000:unit", "verdict": "major", "issue": "wrong term"}]},
                      {"verdicts": [{"id": "u0000:codemix", "verdict": "ok"}]})
    result = Translator(client, judge, [Term("linear pair", "technical")]).translate_language(
        [unit("u0000", a), unit("u0001", b), unit("u0002", c)], "hi")
    assert [u.chosen for u in result.units] == ["codemix", "unit", "unit"]  # 1 English vs 2 native: keep native
    assert result.harmonised == [] and result.summary()["terms"]["still_mixed"] == ["linear pair"]
