"""Error injector: exact labels, determinism, and that the checks find what was planted."""

import asyncio
import random
from dataclasses import dataclass

import numpy as np

from bodhan_eval.checks.translation import check_numbers
from bodhan_eval.dataset import Item
from bodhan_eval.inject import audio as A
from bodhan_eval.inject import text as T
from bodhan_eval.inject.run import inject_dataset
from bodhan_eval.languages import load_languages
from bodhan_eval.params import Params

L = load_languages()
SRC = "The tower is 330 metres tall. It was completed in 1889. Millions of people visit it."
TR = "टॉवर 330 मीटर ऊँचा है। इसे 1889 में पूरा किया गया था। लाखों लोग इसे देखने आते हैं।"
POOL = ["यह एक बिल्कुल अलग वाक्य है।"]


def ctx(src=SRC, tr=TR, lang="hi"):
    return T.Ctx(src, tr, lang, lang, POOL, ["Germany"])


def rng(s=0):
    return random.Random(s)


def test_omission_marks_deletion_point_and_source():
    for seed in range(6):
        inj = T.omission(ctx(), rng(seed))
        s, e = inj.label["span"]
        assert s == e and len(inj.translation) < len(TR)
        src = SRC[slice(*inj.label["source_span"])]
        assert src  # sentences align one to one here
        assert inj.translation.count("।") == 2


def test_number_changes_one_digit_and_keeps_digit_system():
    inj = T.number(ctx(), rng(1))
    s, e = inj.label["span"]
    assert inj.translation[s:e] != TR[s:e] and inj.translation[s:e].isdigit()
    assert len(inj.translation) == len(TR)
    bn_src, bn_tr = "It costs 250 rupees.", "এর দাম ২৫০ টাকা।"
    inj = T.number(ctx(bn_src, bn_tr, "bn"), rng(2))
    s, e = inj.label["span"]
    new = inj.translation[s:e]
    assert new != "২৫০" and all("০" <= ch <= "৯" for ch in new)  # still Bengali digits
    flags = check_numbers(bn_src, inj.translation, L["bn"], Params.defaults()).flags
    assert flags and flags[0].span == (s, e)  # the rule-based check finds it exactly


def test_addition_span_is_inserted_sentence():
    inj = T.addition(ctx(), rng(3))
    s, e = inj.label["span"]
    assert inj.translation[s:e] == POOL[0]


def test_wrong_language_restores_english_sentence():
    inj = T.wrong_language(ctx(), rng(4))
    s, e = inj.label["span"]
    assert inj.translation[s:e] in SRC


def test_format_break_variants():
    tr = "## शीर्षक\n\n| क | ख |\n|---|---|\n| 1 | 2 |\n\nसूत्र $x^2$ और **ज़रूरी** है।"
    seen = set()
    for seed in range(30):
        inj = T.format_break(ctx("x", tr), rng(seed))
        seen.add(inj.label["injection"])
        s, e = inj.label["span"]
        assert 0 <= s <= e <= len(inj.translation) and inj.translation != tr
    assert seen == {"break_table", "break_heading", "break_math", "break_bold"}
    assert T.format_break(ctx(), rng(0)) is None  # nothing to break in plain text


def test_perturb_meaning():
    assert T.perturb_meaning("Prices increase in winter.", rng(0)) == "Prices decrease in winter."
    out = T.perturb_meaning("The road is open.", random.Random(5))
    assert out in ("The road is closed.", "The road is not open.")
    assert T.perturb_meaning("Hello there.", rng(0)) is None


@dataclass
class _R:
    text: str
    truncated: bool = False


class FakeTranslator:
    def __init__(self):
        self.calls = []

    async def translate(self, text, target_language, script="native"):
        self.calls.append(text)
        return _R(f"<{text}>")


def test_retranslate_mistranslation_and_term():
    fake = FakeTranslator()
    src = "Paris is the capital of France. Prices increase in winter."
    tr = "पेरिस फ्रांस की राजधानी है। सर्दियों में कीमतें बढ़ती हैं।"
    c = T.Ctx(src, tr, "hi", "hi", POOL, ["Germany"])
    inj = asyncio.run(T.mistranslation(c, rng(0), fake))
    s, e = inj.label["span"]
    assert inj.translation[s:e] == f"<{inj.label['perturbed_source']}>"
    inj = asyncio.run(T.term(c, rng(0), fake))
    assert inj.label["replacement"] == "Germany" and "Germany" in fake.calls[-1]


# --- audio ------------------------------------------------------------------------------

SR = 24000


def speechlike(sec=6.0):
    t = np.arange(int(sec * SR)) / SR
    env = (np.sin(2 * np.pi * 1.5 * t) > -0.3).astype(np.float32)  # pauses between "words"
    return (0.3 * np.sin(2 * np.pi * 180 * t) * env).astype(np.float32)


def test_audio_injections_change_audio_and_label_time():
    x = speechlike()
    for name, fn in A.AUDIO.items():
        y, lab = fn(x, SR, 100, rng(7))
        assert lab["area"] == "speech" and lab["span_approx"]
        t0, t1 = lab["time_span"]
        assert 0 <= t0 <= t1
        if name == "truncate":
            assert len(y) < len(x) and lab["span"][1] == 100
        elif name in ("repeat", "silence"):
            assert len(y) > len(x)
        else:
            assert len(y) == len(x) and not np.allclose(y, x)


# --- dataset ----------------------------------------------------------------------------


def _items(n=20):
    return [Item(id=f"i{k}", source=SRC, lang="hi", translation=TR) for k in range(n)]


def test_inject_dataset_rate_scope_and_determinism(tmp_path):
    rows1, stats = asyncio.run(inject_dataset(_items(), L, tmp_path, rate=0.5, seed=3))
    rows2, _ = asyncio.run(inject_dataset(_items(), L, tmp_path, rate=0.5, seed=3))
    assert rows1 == rows2
    injected = [r for r in rows1 if r["label_scope"] == "injected"]
    controls = [r for r in rows1 if r["label_scope"] == "assumed_clean"]
    assert len(injected) + len(controls) == 20 and 4 <= len(injected) <= 16
    assert all(r["labels"] == [] and r["translation"] == TR for r in controls)
    for r in injected:
        assert r["id"].startswith(r["injected_from"] + "__")
        assert r["labels"][0]["annotator"] == "injector"
    assert sum(v for k, v in stats["counts"].items() if k.startswith("injected:")) == len(injected)


def test_inject_dataset_keeps_existing_labels(tmp_path):
    from bodhan_eval.dataset import Label

    it = Item(
        id="h1",
        source=SRC,
        lang="hi",
        translation=TR,
        labels=[Label("number", "major", (4, 7), {"type": "number"})],
    )
    rows, stats = asyncio.run(inject_dataset([it], L, tmp_path, rate=1.0))
    assert rows[0]["labels"] == [{"type": "number"}] and stats["counts"] == {"copied": 1}


def test_term_skips_units_and_table_headers():
    fake = FakeTranslator()
    src = "| Train | Departure |\n|---|---|\n| Rajdhani Express | 16:55 |"
    tr = "| ট্রেন | ছাড়ার সময় |\n|---|---|\n| রাজধানী এক্সপ্রেস | ১৬:৫৫ |"
    c = T.Ctx(src, tr, "bn", "bn", POOL, ["Germany"])
    inj = asyncio.run(T.term(c, rng(0), fake))
    assert inj is None or inj.label["replaced"] == "Rajdhani Express"
    assert T._swappable("Water boils at 100 degrees Celsius.", "Celsius", (27, 34)) is False


def test_wrong_language_never_picks_table_rules():
    src = "| Train | Time |\n|---|---|\n| Rajdhani Express | 16:55 |"
    tr = "| ரயில் | நேரம் |\n|---|---|\n| ராஜ்தானி எக்ஸ்பிரஸ் | 16:55 |"
    for seed in range(20):
        inj = T.wrong_language(ctx(src, tr, "ta"), rng(seed))
        assert inj.translation != tr  # something really changed
        s, e = inj.label["span"]
        assert "---" not in inj.translation[s:e] or inj.label["injection"] == "untranslated_text"
