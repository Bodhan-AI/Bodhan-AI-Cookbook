import io
import random

import soundfile as sf
from conftest import FakeBodhan, to_wav, tone

from bodhan_eval.inject import Injector, _diff_span
from bodhan_eval.normalize import numbers

ITEM = {"id": "x", "lang": "hi", "source": "Water boils at 100 °C at sea level.", "translation": "पानी समुद्र तल पर 100 °C पर उबलता है।"}


def make(seed=1):
    return Injector(FakeBodhan(), random.Random(seed), {"hi": ["यह एक अतिरिक्त वाक्य है।"], "names": ["Delhi"]})


def test_number_injection_changes_one_number_and_labels_it():
    new, label = make().number(dict(ITEM))
    assert new != ITEM["translation"]
    assert [v for v, _, _ in numbers(new)] != ["100"]
    assert new[slice(*label["span"])].isdigit()


def test_number_injection_keeps_native_digit_script():
    item = dict(ITEM, translation="পানি ১০০ ডিগ্রিতে ফোটে।", lang="bn")
    new, label = make().number(item)
    assert all("০" <= c <= "৯" for c in new[slice(*label["span"])])


def test_addition_label_covers_inserted_text():
    new, label = make().addition(dict(ITEM))
    assert new[slice(*label["span"])] == "यह एक अतिरिक्त वाक्य है।"


def test_format_injection_breaks_markdown():
    item = dict(ITEM, translation="| क | ख |\n|---|---|\n| 1 | 2 |")
    new, label = make().format(item)
    assert new.count("|") == item["translation"].count("|") - 1
    assert label["type"] == "format"


def test_diff_span_rejects_global_rewrites():
    assert _diff_span("abcdefgh", "abcXefgh") == (3, 4)
    assert _diff_span("abcdefgh", "zzzzzzzz") is None


def test_speech_omission_drops_a_segment():
    sr = 24000
    wav = to_wav(__import__("numpy").concatenate([tone(1.0), tone(0.25, amp=0), tone(1.0)]))
    segs = [{"span": [0, 10], "start_s": 0.0, "end_s": 1.0}, {"span": [11, 20], "start_s": 1.25, "end_s": 2.25}]
    out, label = make().speech("speech_omission", wav, segs, "x" * 20)
    assert sf.info(io.BytesIO(out)).frames < 2.25 * sr * 0.8
    assert label["type"] == "speech_omission" and label["span"] in ([0, 10], [11, 20])
