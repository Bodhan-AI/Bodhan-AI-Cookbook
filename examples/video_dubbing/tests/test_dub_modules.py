import numpy as np
import soundfile as sf

from bodhan_dub.condense import condense, validate_condensed
from bodhan_dub.dub_language import Deps, DubConfig, dub_language, process_segment
from bodhan_dub.fit import budget_for, condense_target_chars, plan_fit
from bodhan_dub.qa import judge, masked_error, tokens
from bodhan_dub.schedule import schedule
from bodhan_dub.stretch import apply_speed

SR = 24000


# ---------------------------------------------------------------- fit
def test_budget_uses_real_speech_window_and_gap():
    items = [{"start": 0.0, "end": 4.0}, {"start": 5.0, "end": 9.0}, {"start": 9.1, "end": 12.0}]
    b0 = budget_for(0, items, 20.0)
    assert b0["target"] == 4.0 and b0["budget"] == 4.25          # end + END_SLACK (gap is 1.0 s)
    b1 = budget_for(1, items, 20.0)
    assert b1["budget"] == 4.0                                     # gap 0.1 s: no slack at all
    b2 = budget_for(2, items, 20.0)
    assert b2["budget"] == 3.15                                    # last unit: slack before video end


def test_budget_short_unit_borrows_silence():
    items = [{"start": 0.0, "end": 0.5}, {"start": 3.0, "end": 4.0}]
    assert budget_for(0, items, 10.0)["budget"] == 1.0


def test_plan_fit_keep():
    assert plan_fit(4.0, 4.25, 4.0)["action"] == "keep"


def test_plan_fit_shrinks_pauses_before_speeding_up():
    p = plan_fit(4.5, 4.25, 4.0, max_shrink=0.4)
    assert p["action"] == "fit" and p["speed"] == 1.0 and abs(p["pause_delta"] + 0.25) < 1e-6


def test_plan_fit_speeds_up_after_pauses():
    p = plan_fit(5.0, 4.25, 4.0, max_shrink=0.25)
    assert p["action"] == "fit" and p["pause_delta"] == -0.25 and abs(p["speed"] - 4.75 / 4.25) < 1e-3


def test_plan_fit_condense_when_too_long():
    p = plan_fit(7.0, 4.25, 4.0, max_shrink=0.2)
    assert p["action"] == "condense" and p["speed"] == 1.2


def test_plan_fit_widens_pauses_then_slows_down():
    p = plan_fit(3.0, 4.25, 4.0, max_extend=0.5)
    assert p["action"] == "fit" and p["pause_delta"] == 0.5
    assert abs(p["speed"] - 3.5 / 4.0) < 1e-3 and p["speed"] >= 0.85
    p2 = plan_fit(2.0, 4.25, 4.0, max_extend=0.0)
    assert p2["speed"] == 0.85                                     # never slower than 0.85


def test_condense_target_never_grows():
    assert condense_target_chars(100, 10.0, 5.0) < 100
    assert condense_target_chars(100, 2.0, 5.0) == 100


# ---------------------------------------------------------------- schedule
def _it(i, start, end, dur, lead=0.03, voiced=None):
    voiced = dur - 0.03 if voiced is None else voiced
    return {"id": i, "start": start, "end": end, "dur": dur, "lead": lead, "voiced_end": voiced}


def test_schedule_first_sound_on_source_start():
    out = schedule([_it(1, 1.0, 3.0, 2.06), _it(2, 5.0, 7.0, 2.06)], 20.0)
    assert [o["onset"] for o in out] == [1.0, 5.0]
    assert out[0]["placed_start"] == 0.97 and all(o["delay"] == 0 for o in out)
    assert all(not o["flags"] for o in out)


def test_schedule_delays_only_when_previous_still_sounding():
    out = schedule([_it(1, 0.0, 2.0, 3.0), _it(2, 2.5, 4.0, 1.0)], 20.0)
    # first sounds until ~2.97; second must wait for it + 0.2 s gap
    assert out[1]["onset"] >= out[0]["voiced_end_at"] + 0.2 - 1e-6
    assert "delayed" in out[1]["flags"] and "sync_miss" in out[1]["flags"]
    assert "late_end" in out[0]["flags"]


def test_schedule_contiguous_source_needs_no_gap():
    out = schedule([_it(1, 0.0, 2.0, 2.06), _it(2, 2.0, 4.0, 2.06)], 20.0)
    assert out[1]["delay"] == 0.0


def test_schedule_flags_past_end():
    assert "past_end" in schedule([_it(1, 9.0, 10.0, 3.0)], 10.0)[0]["flags"]


# ---------------------------------------------------------------- stretch
def test_apply_speed_changes_duration(tmp_path):
    sf.write(tmp_path / "a.wav", np.sin(np.linspace(0, 2000, SR * 2)).astype("float32") * 0.3, SR)
    apply_speed(tmp_path / "a.wav", tmp_path / "b.wav", 1.25)
    assert abs(sf.info(tmp_path / "b.wav").duration - 2.0 / 1.25) < 0.05
    apply_speed(tmp_path / "a.wav", tmp_path / "c.wav", 0.85)
    assert abs(sf.info(tmp_path / "c.wav").duration - 2.0 / 0.85) < 0.05


# ---------------------------------------------------------------- qa
def test_tokens_keep_devanagari_marks_and_drop_danda():
    assert tokens("माइटोकॉन्ड्रिया काम करते हैं।") == ["माइटोकॉन्ड्रिया", "काम", "करते", "हैं"]


def test_qa_identical_is_zero():
    assert masked_error("यह एक वाक्य है", "यह एक वाक्य है")["cer"] == 0


def test_qa_digits_and_latin_are_wildcards():
    assert masked_error("2019 में लगभग ATP बनता है", "ट्वेंटी नाइनटीन में लगभग एटीपी बनता है")["cer"] == 0


def test_qa_detects_omission_and_repeat():
    ref = "यह कोशिका बहुत अक्षम थी लेकिन फिर इसने कुछ किया"
    assert "omission" in judge(ref, "यह कोशिका बहुत अक्षम थी")["flags"]
    assert "repeat" in judge(ref, ref + " कुछ किया कुछ किया कुछ किया कुछ किया")["flags"]
    assert judge(ref, ref)["ok"]


# ---------------------------------------------------------------- condense
def test_validate_condensed():
    old = "So it is actually the situation that a primitive cell faced 5 years ago."
    assert validate_condensed(old, "It is the situation a primitive cell faced 5 years ago.") is None
    assert validate_condensed(old, old) == "not shorter"
    assert validate_condensed(old, "Cell.") == "too short"
    assert validate_condensed(old, "It is the situation a primitive cell faced 9 years ago.") == "new numbers"
    assert validate_condensed(old, "Quantum computers revolutionize modern banking systems worldwide.") == "too many new words"


def test_validate_condensed_allows_rewording_short_sentences():
    assert validate_condensed("It will decide how you age.", "It decides your aging.") is None
    assert validate_condensed("It will decide how you age.", "Bananas grow in warm tropical climates.") is not None


def fake_llm(answer, judge="YES"):
    """Fake LLM: `answer` for the shorten prompt, `judge` for the meaning check."""
    def llm(msgs):
        return judge if "Reply with exactly one word" in msgs[0]["content"] else (answer(msgs) if callable(answer) else answer)
    return llm


def test_condense_returns_none_on_bad_or_failed_llm():
    old = "So it is actually the situation that a primitive cell faced long ago."
    good = "It is the situation a primitive cell faced long ago."
    assert condense(old, 40, llm=fake_llm(good)) == good
    assert condense(old, 40, llm=fake_llm(old)) is None

    def boom(m):
        raise RuntimeError("down")

    assert condense(old, 40, llm=boom) is None


def test_condense_rejects_meaning_change():
    old = "It will decide how you age."
    assert condense(old, 20, llm=fake_llm("It decides your age.", judge="NO")) is None
    assert condense(old, 20, llm=fake_llm("It decides your age.", judge="Yes, mostly.")) is not None
    assert condense(old, 20, llm=fake_llm("It decides your age.", judge="maybe")) is None


def test_condense_second_try_gets_feedback():
    seen = []

    def answer(msgs):
        seen.append(msgs[1]["content"])
        return "It decides your age." if len(seen) == 1 else "It decides how you age."

    judged = iter(["NO", "YES"])
    llm = lambda m: next(judged) if "Reply with exactly one word" in m[0]["content"] else answer(m)
    assert condense("It will decide how you age.", 20, llm=llm) == "It decides how you age."
    assert "rejected (meaning changed)" in seen[1]


# ---------------------------------------------------------------- orchestrator with fake services
def fake_clip(path, voiced, pauses=(), lead=0.08, tail=0.06):
    """Noise 'speech' of `voiced` seconds (pauses inside), with silent lead/tail."""
    rng = np.random.default_rng(0)
    w = [np.zeros(int(SR * lead), dtype="float32")]
    spans = list(pauses)
    speech_total = voiced - sum(spans)
    parts = len(spans) + 1
    for i in range(parts):
        w.append((rng.standard_normal(int(SR * speech_total / parts)) * 0.2).astype("float32"))
        if i < len(spans):
            w.append(np.zeros(int(SR * spans[i]), dtype="float32"))
    w.append(np.zeros(int(SR * tail), dtype="float32"))
    sf.write(path, np.concatenate(w), SR)


def make_deps(tmp_path, sec_per_char=0.1, fail_qa_first=False, tts_fails=False, pauses=()):
    state = {"calls": 0, "qa_calls": 0}

    def tts(text, variant):
        state["calls"] += 1
        if tts_fails:
            return {"ok": False, "error": "boom", "chars": len(text), "dur": 0, "cached": False}
        path = tmp_path / f"t_{abs(hash((text, variant)))}.wav"
        fake_clip(path, len(text) * sec_per_char, pauses)
        return {"ok": True, "path": str(path), "dur": len(text) * sec_per_char, "chars": len(text), "cached": False}

    def qa(path, ref):
        state["qa_calls"] += 1
        if fail_qa_first and state["qa_calls"] == 1:
            return {"ok": False, "cer": 0.9, "flags": ["omission"], "cached": False}
        return {"ok": True, "cer": 0.0, "flags": [], "cached": False}

    return Deps(tts=tts, qa=qa, translate=lambda en: "x" * 30,
                condense=lambda en, t, p, n: en[: max(5, len(en) // 2)]), state


def seg(i, start, end, tr, src="Some English source text here.", inner=0.0):
    return {"id": i, "start": start, "end": end, "translation": tr, "src": src, "inner_pause": inner}


BUD = {"target": 5.0, "budget": 5.25}


def test_process_segment_keeps_a_fitting_clip(tmp_path):
    deps, _ = make_deps(tmp_path)
    rec = process_segment(seg(1, 0, 5, "x" * 50), BUD, "", "", DubConfig(), deps)   # 5.0 s voiced
    assert rec["status"] == "ok" and rec["fit"]["action"] == "keep" and len(rec["tts_log"]) == 1


def test_process_segment_condenses_when_too_long(tmp_path):
    deps, _ = make_deps(tmp_path)
    rec = process_segment(seg(1, 0, 5, "x" * 80), BUD, "", "", DubConfig(), deps)   # 8.0 s voiced
    assert rec["condense_rounds"] >= 1 and rec["tts_voiced"] < 8.0 and rec["fit"]["action"] != "condense"


def test_process_segment_retries_after_qa_failure(tmp_path):
    deps, _ = make_deps(tmp_path, fail_qa_first=True)
    rec = process_segment(seg(1, 0, 5, "x" * 50), BUD, "", "", DubConfig(), deps)
    assert rec["status"] == "ok" and rec["qa_attempts"] == 2 and rec["variant"] == 1


def test_process_segment_tts_failure_is_recorded(tmp_path):
    deps, _ = make_deps(tmp_path, tts_fails=True)
    rec = process_segment(seg(1, 0, 5, "x" * 50), BUD, "", "", DubConfig(), deps)
    assert rec["status"] == "failed" and len(rec["tts_log"]) == 3


def test_dub_language_end_to_end(tmp_path):
    deps, _ = make_deps(tmp_path)
    segs = [seg(1, 0.0, 4.0, "x" * 38), seg(2, 5.0, 9.0, "x" * 44), seg(3, 10.0, 14.0, "")]
    recs, summary = dub_language(segs, 20.0, tmp_path, DubConfig(vad_backend="energy"), deps)
    assert summary["dubbed"] == 2 and summary["skipped"] == 1 and summary["failed"] == 0
    placed = [r for r in recs if "clip_path" in r]
    assert all(0.85 <= r["speed"] <= 1.2 for r in placed)
    assert all(abs(r["start_err"]) < 1e-6 for r in placed)          # first sound exactly on the source start
    assert (tmp_path / "hi" / "dub.hi.json").is_file()


def test_dub_language_until_limits_segments(tmp_path):
    deps, _ = make_deps(tmp_path)
    segs = [seg(1, 0.0, 4.0, "x" * 38), seg(2, 5.0, 9.0, "x" * 40)]
    recs, _ = dub_language(segs, 20.0, tmp_path, DubConfig(until=4.5, vad_backend="energy"), deps)
    assert [r["id"] for r in recs] == [1]
