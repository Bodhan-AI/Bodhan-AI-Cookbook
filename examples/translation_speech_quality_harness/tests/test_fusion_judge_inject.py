"""Fusion, calibration, the judge's parsing/guard, the injector, and label aliases."""
import asyncio
import random

import numpy as np

from bodhan_eval.calibrate import evaluate, tune
from bodhan_eval.checks import Context, Signal
from bodhan_eval.dataset import Item, Label
from bodhan_eval.fuse import cluster_signals, flags_for, load_thresholds
from bodhan_eval.inject import text_injector as ti
from bodhan_eval.inject.audio_injector import SPEECH_INJECTORS
from bodhan_eval.judge.judge import Judge, sanitize
from bodhan_eval.langs import get_lang
from bodhan_eval.taxonomy import canonical_category

CFG = load_thresholds()
CFG["thresholds"] = {c: 0.55 for c in CFG["thresholds"]}  # fixed, so tests do not depend on calibrated defaults


def test_noisy_checker_alone_cannot_flag_but_corroboration_can():
    bt = Signal("backtranslation", "omission", "major", 0.5, "gap", None, (0, 10))
    assert flags_for(cluster_signals([bt]), CFG) == []
    judge = Signal("judge", "omission", "major", 0.7, "missing clause", (5, 9), (0, 10))
    fl = flags_for(cluster_signals([bt, judge]), CFG)
    assert len(fl) == 1 and fl[0]["span"] == [5, 9] and set(fl[0]["sources"]) == {"backtranslation", "judge"}


def test_strong_deterministic_signal_flags_alone_and_separate_spans_stay_separate():
    a = Signal("numbers", "number", "critical", 0.95, "100->10", (3, 5))
    b = Signal("numbers", "number", "critical", 0.95, "15->16", (20, 22))
    fl = flags_for(cluster_signals([a, b]), CFG)
    assert len(fl) == 2


def test_verifier_denial_suppresses():
    c = cluster_signals([Signal("judge", "mistranslation", "major", 0.75, "x", (0, 3))])
    assert flags_for(c, CFG)
    c[0].denial = 0.9
    assert flags_for(c, CFG) == []


def test_calibration_metrics_and_tuning():
    def rec(labels, sigs, i):
        return {"id": str(i), "lang": "hi", "labels": labels, "clusters": cluster_signals(sigs), "group": str(i)}

    lab = Label(type="number", span=[3, 5])
    records = [rec([lab], [Signal("numbers", "number", "critical", 0.95, "m", (3, 5))], i) for i in range(8)]
    records += [rec([], [Signal("judge", "mistranslation", "major", 0.6, "m", (0, 2))], 100 + i) for i in range(4)]
    records += [rec([], [], 200 + i) for i in range(8)]
    m = evaluate(records, CFG)
    assert m["detection_recall"] == 1.0 and m["localization"] == 1.0 and m["category_accuracy"] == 1.0
    tuned, _ = tune(records, CFG)
    assert evaluate(records, tuned)["false_alarm_rate"] <= 0.10


def test_label_aliases():
    assert canonical_category("truncation") == "speech_omission"
    assert canonical_category("omission", "speech") == "speech_omission"
    assert canonical_category("term_or_entity") == "term"
    assert canonical_category("Number or unit") == "number"
    assert canonical_category("untranslated") == "wrong_language"


def test_sanitize_strips_indic_text_from_llm_output():
    s, changed = sanitize("The word पानी was changed to 10.")
    assert changed and "पानी" not in s and "10" in s


class FakeLLM:
    model = "fake"

    def __init__(self, reply):
        self.reply = reply

    async def chat(self, messages, max_tokens=1500, sample=0):
        return self.reply


def test_judge_maps_token_indices_to_exact_spans():
    tr = "समुद्र तल पर पानी 10 डिग्री पर उबलता है।"
    ctx = Context(Item(id="x", source="Water boils at 100 degrees at sea level.", lang="hi"), get_lang("hi"), tr, {})
    reply = '```json\n{"errors": [{"category": "number", "severity": "critical", "tokens": [4, 4], "source_quote": "100 degrees", "explanation": "10 instead of 100 (लिखा)", "confidence": 0.9}]}\n```'
    sigs, meta = asyncio.run(Judge(FakeLLM(reply)).judge(ctx))
    assert len(sigs) == 1 and tr[slice(*sigs[0].span)] == "10"
    assert ctx.item.source[slice(*sigs[0].source_span)] == "100 degrees"
    assert meta.get("sanitized") and "लिखा" not in sigs[0].message


def test_judge_unparseable_reply_is_safe():
    ctx = Context(Item(id="x", source="Hi.", lang="hi"), get_lang("hi"), "नमस्ते।", {})
    sigs, meta = asyncio.run(Judge(FakeLLM("no json here")).judge(ctx))
    assert sigs == [] and meta["status"] == "unparseable"


def test_text_injectors_produce_valid_spans():
    rng = random.Random(1)
    tr = "पानी 100 डिग्री पर उबलता है। पहाड़ पर यह 90 डिग्री पर उबलता है।"
    src = "Water boils at 100 degrees. On a mountain it boils at 90 degrees."
    new, lab = ti.number(tr, src, rng)
    a, b = lab["span"]
    assert new != tr and new[a:b] != tr[a:b] and new[a:b].isdigit()
    new, lab = ti.omission(tr, src, rng)
    assert len(new) < len(tr) and lab["span"][0] == lab["span"][1]
    new, lab = ti.addition(tr, "यह एक अतिरिक्त वाक्य है।", rng)
    assert "अतिरिक्त" in new[lab["span"][0]:lab["span"][1]]
    md = "| A | B |\n|---|---|\n| 1 | 2 |"
    new, lab = ti.format_break(md, rng)
    assert new.count("|") == md.count("|") - 1


def test_english_perturbation_and_diff_span():
    p = ti.perturb_source("Isaac Newton described the laws of motion.", "term", random.Random(0))
    assert p and "Galileo" in p[0]
    clean, pert = "न्यूटन ने गति के नियम बताए।", "गैलीलियो ने गति के नियम बताए।"
    s, e = ti.diff_span(clean, pert)
    assert pert[s:e] == "गैलीलियो"


def test_audio_injectors_keep_length_sensible():
    sr = 16000
    x = np.ones(sr * 4, dtype=np.float32) * 0.1
    segs = [{"start_s": 0.0, "end_s": 1.9, "text_span": [0, 10]}, {"start_s": 2.1, "end_s": 4.0, "text_span": [11, 20]}]
    rng = random.Random(0)
    y, lab = SPEECH_INJECTORS["truncation"](x, sr, segs, 20, rng)
    assert len(y) < len(x) and lab["span"] == [11, 20]
    y, lab = SPEECH_INJECTORS["repetition"](x, sr, segs, 20, rng)
    assert len(y) > len(x)
