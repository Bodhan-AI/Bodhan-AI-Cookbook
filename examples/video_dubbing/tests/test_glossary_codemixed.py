"""--glossary and --code-mixed. Target-language strings are real Indic-Translate / Indic-Transcribe outputs
(live probes 2026-11-20, and the demo's Tamil track), not invented text."""

import json
import sys

import pytest

from bodhan_dub import __main__ as M
from bodhan_dub import dub_language as D
from bodhan_dub import glossary as G
from bodhan_dub import pipeline as P
from bodhan_dub import translate as T
from bodhan_dub.condense import _cache_file, condense, validate_condensed
from bodhan_dub.qa import judge, tokens

# ---------------------------------------------------------------- real outputs
CODE_MIXED = {   # "Translate the following text into <Language> (code-mixed):"
    "hi": ["आज हम नए machine learning model को cloud server पर deploy करेंगे।",
           "नब्बे degrees से बड़े कोण को obtuse angle कहा जाता है।",
           "कृपया अपने phone को restart करें और meeting से पहले app को update करें।"],
    "ta": ["இன்று நாம் புதிய machine learning model-ஐ cloud server-இல் deploy செய்வோம்.",
           "ninety degrees-ஐ விட பெரிய angle-ஐ obtuse angle என்று அழைக்கப்படுகிறது.",
           "meetingக்கு முன்னாடி உங்க phoneஐ restart பண்ணி, app update பண்ணுங்க."],
    "bn": ["আজ আমরা নতুন machine learning modelটি cloud server-এ deploy করব।",
           "নব্বই degrees-এর চেয়ে বড় angle-কে obtuse angle বলা হয়।",
           "meeting এর আগে আপনার phone restart করুন এবং app update করুন।"],
}
CM_SRC = ["Today we will deploy the new machine learning model to the cloud server.",
          "An angle bigger than ninety degrees is called an obtuse angle.",
          "Please restart your phone and update the app before the meeting."]
CM_ASR = {   # the first sentence, spoken by Indic-Speak and transcribed back
    "hi": "आज हम नए मशीन लर्निंग मॉडल को क्लाउड सर्वर पर डिप्लॉय करेंगे",
    "ta": "இன்று நாம் புதிய மெஷின் லேர்னிங் மாடலை க்ளவுட் சர்வரில் டிப்ளாய் செய்வோம்",
    "bn": "আজ আমরা নতুন মেশিন লার্নিং মডেলটি ক্লাউড সার্ভারে ডিপ্লয় করব",
}
TA_ANGLE_FORMS = {"கோணம்": 12, "கோணங்கள்": 7, "கோணங்கள்.": 3, "கோணங்களின்": 2, "கோண": 1, "கோணங்களாக": 1}
# Bengali "angle": Indic-Translate says কোণ; the glossary wants the loanword এঙ্গেল (its own transliteration).
BN_FIXED = {"src": "Two adjacent angles form a linear pair.",
            "plain": "দুটি সন্নিহিত কোণ একটি সরল যুগল গঠন করে।",
            "forced": "Two adjacent এঙ্গেল form a linear pair.",
            "retry": "দুটি সন্নিহিত এঙ্গেল একটি লিনিয়ার পেয়ার গঠন করে।"}
BN_NOT_FIXED = {"src": "An angle bigger than ninety degrees is called an obtuse angle.",
                "plain": "নব্বই ডিগ্রির চেয়ে বড় কোণকে স্থূলকোণ বলা হয়।",
                "forced": "An এঙ্গেল bigger than ninety degrees is called an obtuse এঙ্গেল.",
                "retry": "নব্বই ডিগ্রির চেয়ে বড় একটি কোণকে স্থূলকোণ বলা হয়।"}
BN_GLOSSARY = {"angle": {"bn": "এঙ্গেল"}}


# ---------------------------------------------------------------- loading
def _write(tmp_path, text, name="g.yaml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_load_term_first_language_first_and_entry_list_give_the_same_glossary(tmp_path):
    term_first = _write(tmp_path, "angle:\n  bn: এঙ্গেল\n  hi: कोण\n", "a.yaml")
    lang_first = _write(tmp_path, "bn:\n  angle: এঙ্গেল\nhi:\n  angle: कोण\n", "b.yaml")
    entries = _write(tmp_path, "terms:\n  - term: angle\n    bn: এঙ্গেল\n    hi: कोण\n", "c.yaml")
    nested = _write(tmp_path, "- en: angle\n  translations: {bn: এঙ্গেল, HI: कोण}\n", "d.yaml")
    want = {"angle": {"bn": "এঙ্গেল", "hi": "कोण"}}
    for p in (term_first, lang_first, entries, nested):
        assert G.load_glossary(p) == want, p.name


def test_load_same_in_every_language_and_keep_as_is(tmp_path):
    g = G.load_glossary(_write(tmp_path, "NASA: NASA\nISRO:\n"))
    assert g == {"NASA": {"*": "NASA"}, "ISRO": {"*": "ISRO"}}
    assert G.load_glossary(_write(tmp_path, "- ATP\n- DNA\n", "l.yaml")) == {"ATP": {"*": "ATP"}, "DNA": {"*": "DNA"}}
    assert G.renderings_for({"x": {"*": "A", "hi": "B"}}, "hi") == {"x": "B"}
    assert G.renderings_for({"x": {"*": "A", "hi": "B"}}, "ta") == {"x": "A"}


def test_load_skips_what_it_cannot_use_and_never_crashes(tmp_path, caplog):
    g = G.load_glossary(_write(tmp_path, "angle:\n  bn: এঙ্গেল\n  xx: foo\n  ta: [a, b]\n"))
    assert g == {"angle": {"bn": "এঙ্গেল"}}
    assert "skipped 2" in caplog.text
    assert G.load_glossary(_write(tmp_path, "angle: [unclosed\n", "bad.yaml")) == {}      # not YAML
    assert G.load_glossary(_write(tmp_path, "just a sentence\n", "s.yaml")) == {}          # not a mapping
    assert G.load_glossary(_write(tmp_path, "", "e.yaml")) == {}                           # empty


def test_missing_glossary_file_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="glossary file not found"):
        G.load_glossary(tmp_path / "nope.yaml")


# ---------------------------------------------------------------- matching
def test_english_matching_plurals_case_and_acronyms():
    assert G.match_en("angle", "Two adjacent Angles form a pair.")
    assert G.match_en("energy", "Cells store energies.")
    assert G.match_en("box", "three boxes")
    assert G.match_en("machine learning", "a machine\n learning model")
    assert not G.match_en("angle", "a triangle")                   # whole words only
    assert G.match_en("US", "the US economy") and not G.match_en("US", "tell us")   # acronyms: case matters
    assert G.match_en("C++", "written in C++ today")


def test_target_matching_accepts_case_endings_measured_on_the_tamil_demo():
    words = [w for w, n in TA_ANGLE_FORMS.items() for _ in range(n)]
    assert len(words) == 26
    assert sum(G.match_target("கோணம்", w) for w in words) == 26              # stem rule
    assert sum(w.strip(".") == "கோணம்" for w in words) == 12                 # exact match would miss 14


def test_target_matching_other_scripts_and_negatives():
    assert G.match_target("कोण", "दो आसन्न कोणों का योग")
    assert not G.match_target("कोण", "यह त्रिकोण है")                     # must start the word
    assert G.match_target("এঙ্গেল", "বড় এঙ্গেলকে দেখো")
    assert G.match_target("NASA", "NASA-র বিজ্ঞানীরা")
    assert G.match_target("मशीन लर्निंग", "नए मशीन लर्निंग मॉडल")
    assert not G.match_target("मशीन लर्निंग", "मशीन और लर्निंग")


def test_force_terms_replaces_plurals_and_leaves_latin_renderings_alone():
    assert G.force_terms(BN_FIXED["src"], {"angle": "এঙ্গেল"}) == BN_FIXED["forced"]
    assert G.force_terms(BN_NOT_FIXED["src"], {"angle": "এঙ্গেল"}) == BN_NOT_FIXED["forced"]
    assert G.force_terms("NASA launched it.", {"NASA": "NASA"}) == "NASA launched it."


# ---------------------------------------------------------------- code-mixed checks
@pytest.mark.parametrize("code", ["hi", "ta", "bn"])
def test_real_code_mixed_outputs_pass_only_in_code_mixed_mode(code):
    for src, out in zip(CM_SRC, CODE_MIXED[code], strict=True):
        assert T.check_translation(src, out, code, "stop", code_mixed=True)["failures"] == []
        assert "script_ok" in T.check_translation(src, out, code, "stop")["failures"]


def test_code_mixed_still_rejects_untranslated_english_and_wrong_script():
    src = CM_SRC[0]
    assert "script_ok" in T.check_translation(src, src, "hi", "stop", code_mixed=True)["failures"]
    assert "script_ok" in T.check_translation(src, CODE_MIXED["ta"][0], "hi", "stop", code_mixed=True)["failures"]


def test_code_mixed_prompt_name_and_request(tmp_path, monkeypatch):
    assert T.prompt_name("hi", True) == "Hindi (code-mixed)"
    assert T.prompt_name("hi") == "Hindi" and T.prompt_name("en", True) == "English"
    sent = []

    class R:
        ok = True

        def json(self):
            return {"choices": [{"message": {"content": CODE_MIXED["hi"][0]}, "finish_reason": "stop"}]}

    monkeypatch.setattr(T, "post_with_retry", lambda url, key, json: sent.append(json) or R())
    r = T.translate_text(CM_SRC[0], "hi", "k", tmp_path, code_mixed=True)
    assert r["ok"] and sent[0]["messages"][0]["content"].startswith("Translate the following text into Hindi (code-mixed):")
    T.translate_text(CM_SRC[0], "hi", "k", tmp_path)           # plain mode is a different cache entry
    assert len(sent) == 2 and "(code-mixed)" not in sent[1]["messages"][0]["content"]


@pytest.mark.parametrize("code", ["hi", "ta", "bn"])
def test_code_mixed_back_transcription_has_no_false_flags(code):
    j = judge(CODE_MIXED[code][0], CM_ASR[code])
    assert j["ok"], j                                  # ta was a false "omission" (cer 0.154) before the fix
    assert j["cer"] <= 0.1


def test_hyphen_join_only_touches_latin_words():
    assert tokens("model-ஐ") == ["modelஐ"]
    assert tokens("ऊर्जा-संरक्षण") == ["ऊर्जा", "संरक्षण"]     # a native compound is still two words


# ---------------------------------------------------------------- glossary retry
def _fake_translate(table, calls):
    def tr(text, code, key, cache_dir=None, code_mixed=False):
        calls.append(text)
        return {"ok": True, "text": table[text], "finish": "stop", "tokens": 1, "sec": 0.0, "cached": False}
    return tr


def test_translate_units_retries_once_and_keeps_the_retry_only_if_it_helps(tmp_path, monkeypatch):
    table = {BN_FIXED["src"]: BN_FIXED["plain"], BN_FIXED["forced"]: BN_FIXED["retry"],
             BN_NOT_FIXED["src"]: BN_NOT_FIXED["plain"], BN_NOT_FIXED["forced"]: BN_NOT_FIXED["retry"],
             "NASA built it.": "নাসা এটি তৈরি করেছে।"}
    calls: list = []
    monkeypatch.setattr(T, "translate_text", _fake_translate(table, calls))
    monkeypatch.setattr(T, "get_key", lambda s: "k")
    units = [{"id": i, "start": i, "end": i + 1, "text": t}
             for i, t in enumerate([BN_FIXED["src"], BN_NOT_FIXED["src"], "NASA built it."])]
    g = {**BN_GLOSSARY, "NASA": {"*": "NASA"}}
    segs = T.translate_units(units, "bn", tmp_path, mode="unit", glossary=g)
    fixed, kept, latin = segs
    assert fixed["translation"] == BN_FIXED["retry"] and fixed["glossary_retry"] == "fixed"
    assert fixed["status"] == "ok" and fixed["via"] == "unit+glossary_retry"
    assert kept["translation"] == BN_NOT_FIXED["plain"] and kept["glossary_retry"] == "not fixed"
    assert kept["status"] == "flagged" and kept["checks"]["glossary_missing"] == ["angle"]
    assert latin["glossary_retry"] == "no retry" and latin["checks"]["glossary_missing"] == ["NASA"]
    assert calls.count(BN_FIXED["forced"]) == 1 and calls.count(BN_NOT_FIXED["forced"]) == 1
    assert len(calls) == 5                                        # 3 units + 2 retries, nothing for NASA
    saved = json.loads((tmp_path / "bn" / "segments.bn.json").read_text(encoding="utf-8"))
    assert [s["glossary_retry"] for s in saved] == ["fixed", "not fixed", "no retry"]


def test_no_glossary_means_no_retry_and_unchanged_segments(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(T, "translate_text", _fake_translate({BN_FIXED["src"]: BN_FIXED["plain"]}, calls))
    monkeypatch.setattr(T, "get_key", lambda s: "k")
    (s,) = T.translate_units([{"id": 1, "start": 0, "end": 1, "text": BN_FIXED["src"]}], "bn", tmp_path, mode="unit")
    assert s["status"] == "ok" and "glossary_retry" not in s and "glossary_ok" not in s["checks"] and len(calls) == 1


def test_retranslation_after_shortening_uses_code_mixed_and_glossary(tmp_path, monkeypatch):
    seen = []

    def tr(text, code, key, cache_dir=None, code_mixed=False):
        seen.append((text, code_mixed))
        out = {BN_FIXED["src"]: BN_FIXED["plain"], BN_FIXED["forced"]: BN_FIXED["retry"]}.get(text, "নমস্কার")
        return {"ok": True, "text": out, "finish": "stop", "tokens": 1, "sec": 0.0, "cached": False}

    for mod in (D, T):
        monkeypatch.setattr(mod, "translate_text", tr)
    monkeypatch.setattr(D, "get_key", lambda s: "k")
    deps = D.default_deps(D.DubConfig(lang="bn", glossary=BN_GLOSSARY, code_mixed=True), tmp_path)
    assert deps.translate(BN_FIXED["src"]) == BN_FIXED["retry"]
    assert seen == [(BN_FIXED["src"], True), (BN_FIXED["forced"], True)]

    def wrong_script(text, code, key, cache_dir=None, code_mixed=False):
        return {"ok": True, "text": CODE_MIXED["ta"][0], "finish": "stop", "tokens": 1, "sec": 0.0, "cached": False}

    for mod in (D, T):
        monkeypatch.setattr(mod, "translate_text", wrong_script)
    assert deps.translate(BN_FIXED["src"]) is None                 # a real failure still rejects it


# ---------------------------------------------------------------- condensing keeps glossary terms
def test_shortening_must_keep_glossary_terms():
    old = "Two adjacent angles always form a linear pair on a straight line."
    assert validate_condensed(old, "Two adjacent angles form a linear pair.", ("angle",)) is None
    assert validate_condensed(old, "Two adjacent ones form a linear pair.", ("angle",)) == "glossary term lost"
    assert validate_condensed(old, "Two adjacent ones form a linear pair.") is None


def test_condense_names_the_terms_and_keeps_the_old_cache_key_without_them(tmp_path):
    prompts = []

    def llm(messages):
        prompts.append(messages[-1]["content"])
        return "YES" if messages[0]["content"].startswith("You check") else "Two adjacent angles form a linear pair."

    old = "Two adjacent angles always form a linear pair on a straight line."
    assert condense(old, 45, llm=llm, keep_terms=("angle", "NASA")) == "Two adjacent angles form a linear pair."
    assert "Glossary terms (keep each one, word for word): angle\n" in prompts[0]
    assert _cache_file(tmp_path, old, 45, "", "") == _cache_file(tmp_path, old, 45, "", "", ())
    assert _cache_file(tmp_path, old, 45, "", "") != _cache_file(tmp_path, old, 45, "", "", ("angle",))


# ---------------------------------------------------------------- CLI and report
def test_cli_accepts_glossary_and_code_mixed(tmp_path, monkeypatch):
    g = _write(tmp_path, "angle:\n  bn: এঙ্গেল\n")
    got = {}

    def fake_run(*a, **k):
        got.update(k)
        return {"per_language": {"bn": {"status": "ok"}}, "seconds_total": 1, "quality_targets": {}}

    monkeypatch.setattr(M, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["bodhan_dub", "--input", "v.mp4", "--langs", "bn", "--out", str(tmp_path),
                                      "--glossary", str(g), "--voices", "bn=Ishita", "--code-mixed"])
    assert M.main() == 0
    assert got["glossary"] == BN_GLOSSARY and got["code_mixed"] is True and got["voices"] == {"bn": "Ishita"}


def test_cli_stops_on_a_missing_glossary_before_running(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "run", lambda *a, **k: pytest.fail("run must not start"))
    monkeypatch.setattr(sys, "argv", ["bodhan_dub", "--input", "v.mp4", "--langs", "bn", "--out", str(tmp_path),
                                      "--glossary", str(tmp_path / "missing.yaml")])
    with pytest.raises(SystemExit) as e:
        M.main()
    assert e.value.code == 2


def test_report_counts_glossary_uses_on_the_final_text():
    cfg = D.DubConfig(lang="bn", glossary=BN_GLOSSARY)
    recs = [{"id": 1, "status": "ok", "en": BN_FIXED["src"], "text": BN_FIXED["retry"]},
            {"id": 2, "status": "qa_failed", "en": BN_NOT_FIXED["src"], "text": BN_NOT_FIXED["plain"]},
            {"id": 3, "status": "failed", "en": BN_NOT_FIXED["src"], "text": ""}]
    segs = [{"id": 1, "glossary_retry": "fixed"}, {"id": 2, "glossary_retry": "not fixed"}, {"id": 3}]
    rep = P._glossary_report(segs, recs, cfg)
    assert (rep["term_uses"], rep["kept"], rep["missing"]) == (2, 1, 1)
    assert rep["missing_list"] == [{"id": 2, "term": "angle", "wanted": "এঙ্গেল"}]
    assert rep["retry"]["fixed"] == 1 and rep["retry"]["not fixed"] == 1
    assert P._glossary_report(segs, recs, D.DubConfig(lang="bn")) is None
    out = P._segment_out({"id": 2, "start": 0, "end": 1, "src": "x", "status": "ok", "glossary_retry": "not fixed"},
                         recs[1], cfg)
    assert out["glossary_missing"] == ["angle"] and out["glossary_retry"] == "not fixed"
    assert "glossary_missing" not in P._segment_out({"id": 2, "start": 0, "end": 1, "src": "x", "status": "ok"},
                                                    recs[1], D.DubConfig(lang="bn"))
