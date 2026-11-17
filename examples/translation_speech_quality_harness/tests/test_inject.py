"""Injector: deterministic, every label points at its edit, and each recipe works on real Indic-Translate output."""

import json
from pathlib import Path

import numpy as np
import pytest
from conftest import fixture_rows, make_clients, make_wav

from bodhan_eval import inject as inj
from bodhan_eval.checks_speech import read_samples, wav_bytes

ROWS = fixture_rows()


def rows_same_ids():
    """The fixture translations, re-keyed so the same English item has one id in every language."""
    out = []
    for new_id, ids in {"heart": ("t02", "t03", "t04"), "boil": ("t01", "t09"), "nums": ("t05", "t06"), "table": ("t07", "t08")}.items():
        for old in ids:
            r = ROWS[old]
            out.append({"id": new_id, "lang": r["lang"], "source": r["source"], "translation": r["translation"]})
    return out


def ctx(tmp_path, rows, bodhan=None):
    c = {"bodhan": bodhan, "base": tmp_path, "out_dir": tmp_path, "audio_dir": tmp_path / "audio", "by_lang": {}, "by_id": {}}
    for r in rows:
        c["by_lang"].setdefault(r["lang"], []).append(r)
        c["by_id"].setdefault(r["id"], []).append(r)
    return c


def check_label(row, changes, lab):
    """The label's spans are inside the texts, and the text outside the translation span is unchanged."""
    new = changes.get("translation", row["translation"])
    if lab["source_span"]:
        s, e = lab["source_span"]
        assert 0 <= s < e <= len(row["source"])
    s, e = lab["translation_span"]
    assert 0 <= s <= e <= len(new)
    assert lab["span"] == (lab["source_span"] or lab["translation_span"]) and lab["origin"] == "injected"
    if "translation" in changes and not lab["recipe"].startswith(("retranslate", "whole_text")) and e > s:
        old = row["translation"]
        tail = len(new) - e
        assert new[:s] == old[:s] and new[e:] == old[len(old) - tail:]


@pytest.mark.parametrize("recipe", ["omission", "addition", "number", "format", "wrong_language", "term"])
def test_text_recipes_on_real_output(tmp_path, recipe, fake_bodhan, fake_llm):
    rows = rows_same_ids()
    # term re-translates an English edit; the fake answers with the real output plus the one changed word.
    names = {"Hindi": "hi", "Tamil": "ta", "Bengali": "bn"}

    def translate(text, lang):
        for r in rows:
            a, b = r["source"].split(), text.split()
            diff = [w for x, w in zip(a, b) if x != w]
            if r["lang"] == names.get(lang) and len(a) == len(b) and len(diff) == 1:
                return r["translation"].replace(" ", f" {diff[0]} ", 1)
        return text

    fake_bodhan.translate = translate
    c = ctx(tmp_path, rows, make_clients(tmp_path, fake_bodhan, fake_llm)[0])
    hits = 0
    for r in rows:
        for seed in range(5):
            got = inj.RECIPES[recipe](r, c, inj.rng_for(seed, r["id"]))
            if got:
                hits += 1
                changes, lab = got
                assert lab["type"] == recipe and changes["translation"] != r["translation"]
                check_label(r, changes, lab)
    assert hits, f"{recipe} never applied to the fixture rows"


def test_number_respects_digit_script():
    r = ROWS["t05"]  # Hindi output that uses Devanagari digits in one place
    for seed in range(20):
        got = inj.number(r, {}, inj.rng_for(seed))
        changes, lab = got
        s, e = lab["translation_span"]
        old_tok, new_tok = lab["was"], lab["now"]
        assert len(old_tok) == len(new_tok) and old_tok != new_tok
        assert all((a.isascii() == b.isascii()) for a, b in zip(old_tok, new_tok, strict=True))
        assert changes["translation"][s:e] == new_tok


def test_omission_never_drops_table_rows():
    r = ROWS["t07"]
    for seed in range(20):
        got = inj.omission(r, {}, inj.rng_for(seed))
        if got:
            assert not got[1]["removed"].lstrip().startswith("|")


def test_mistranslation_pairs_edited_english_output_with_original_source(tmp_path, fake_bodhan, fake_llm):
    r = ROWS["t01"]
    # The fake model "translates" the edited English by changing one word of the real output.
    first_word = r["translation"].split()[0]
    fake_bodhan.translate = lambda text, lang: r["translation"].replace(first_word, first_word + "X", 1) if "freezes" in text else text
    bodhan, _ = make_clients(tmp_path, fake_bodhan, fake_llm)
    changes, lab = inj.mistranslation(r, ctx(tmp_path, [r], bodhan), inj.rng_for(1))
    assert r["source"][slice(*lab["source_span"])] == "boils"
    s, e = lab["translation_span"]
    assert changes["translation"][s:e] == first_word + "X"


def test_retranslation_recipes_skip_without_a_model(tmp_path):
    assert inj.mistranslation(ROWS["t01"], ctx(tmp_path, []), inj.rng_for(1)) is None


def speech_row(tmp_path):
    # Two tones with pauses between them, standing in for speech.
    tone = read_samples(make_wav(0.8))[0]
    gap = np.zeros(int(0.3 * 24000), dtype=np.float32)
    x = np.concatenate([gap, tone, gap, tone, gap, tone, gap])
    (tmp_path / "a.wav").write_bytes(wav_bytes(x, 24000))
    return {"id": "s1", "lang": "hi", "source": ROWS["t01"]["source"], "translation": ROWS["t01"]["translation"], "audio": "a.wav"}, len(x)


@pytest.mark.parametrize("recipe", ["speech_omission", "speech_repetition", "garbled"])
def test_speech_recipes_write_edited_audio(tmp_path, recipe):
    row, n = speech_row(tmp_path)
    changes, lab = inj.RECIPES[recipe](row, ctx(tmp_path, [row]), inj.rng_for(3))
    y, _ = read_samples((tmp_path / changes["audio"]).read_bytes())
    if recipe == "speech_omission":
        assert len(y) < n
    elif recipe == "speech_repetition":
        assert len(y) > n
    assert lab["type"] == recipe and lab["source_span"] is None and lab["translation_span"][1] <= len(row["translation"])
    assert not Path(changes["audio"]).is_absolute()


def test_speech_recipes_need_audio(tmp_path):
    row = {**ROWS["t01"], "audio": None}
    assert all(inj.RECIPES[c](row, ctx(tmp_path, [row]), inj.rng_for(1)) is None for c in inj.SPEECH)


def test_inject_is_deterministic_and_covers_categories(tmp_path):
    rows = rows_same_ids() * 1
    a = inj.inject(rows, 0.5, 7, tmp_path, tmp_path, tmp_path / "audio")
    b = inj.inject(list(reversed(rows)), 0.5, 7, tmp_path, tmp_path, tmp_path / "audio")
    key = lambda r: (r["id"], r["lang"])  # noqa: E731
    assert sorted(map(json.dumps, sorted(a, key=key))) == sorted(map(json.dumps, sorted(b, key=key)))
    injected = [r for r in a if r["labels"]]
    assert len(injected) == sum(round(0.5 * sum(1 for r in rows if r["lang"] == lang)) for lang in {"hi", "ta", "bn"})
    assert all(len(r["labels"]) <= 1 for r in a)
    c = inj.inject(rows, 0.5, 8, tmp_path, tmp_path, tmp_path / "audio")
    assert [r["labels"] for r in a] != [r["labels"] for r in c]  # a different seed gives a different set


def test_cli(tmp_path):
    src = tmp_path / "clean.jsonl"
    src.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows_same_ids()), encoding="utf-8")
    assert inj.main(["--dataset", str(src), "--out", str(tmp_path / "out" / "inj.jsonl"), "--seed", "3"]) == 0
    out = [json.loads(x) for x in (tmp_path / "out" / "inj.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(out) == 9 and any(r["labels"] for r in out) and any(r["labels"] == [] for r in out)
