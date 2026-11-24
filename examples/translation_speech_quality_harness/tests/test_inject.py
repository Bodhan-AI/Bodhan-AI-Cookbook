"""test_inject: Error injector tests. All Indic text comes from real Bodhan outputs (fixtures);
test_build_demo: scripts/build_demo.py end to end, offline (no model calls), on a fake clean run built from real fixtures.
"""

import ast
import asyncio
import importlib.util
import json
import random
import unicodedata
from pathlib import Path

import numpy as np
import pytest

from bodhan_eval import inject
from bodhan_eval.audio import write_wav
from bodhan_eval.config import CATEGORIES
from bodhan_eval.dataset import load_dataset
from bodhan_eval.inject import CleanItem, Pool, build_labelled_set, changed_span, english_entities, mistranslation, perturb_source, rng_for

# ==================== inject
FIX = Path(__file__).parent / "fixtures"


SMOKE = json.loads((FIX / "bodhan_outputs.json").read_text(encoding="utf-8"))


MD = json.loads((FIX / "bodhan_markdown.json").read_text(encoding="utf-8"))


SEEDS = range(40)


def _texts(fx):
    return {k: (v["translation"] if isinstance(v, dict) else v) for k, v in fx["translations"].items()}


ST, MT = _texts(SMOKE), _texts(MD)  # real Bodhan outputs per language


def item(lang="hi", src="smoke", translation=None, source=None, **kw):
    fx = SMOKE if src == "smoke" else MD
    return CleanItem(
        id=f"{src}-{lang}", src_id=src, lang=lang, source=source or fx["source"], translation=translation or (ST if src == "smoke" else MT)[lang], **kw
    )


def first_diff(a: str, b: str) -> int:
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    return i


@pytest.mark.parametrize("lang", ["hi", "ta", "bn"])
def test_omission_zero_width_span_reconstructs_original(lang):
    one = ST[lang]
    it = item(lang, translation=one + " " + item(lang, "md").translation if lang in MT else one + " " + one)
    kinds = set()
    for seed in SEEDS:
        inj = inject.omission(it, Pool([it]), rng_for(seed, it.id, "omission"))
        assert inj is not None
        (lab,) = inj.labels
        a, b = lab["span"]
        assert a == b, "omission is a zero-width point in the final text"
        deleted = it.translation[a : a + lab["deleted_chars"]]
        assert inj.text[:a] + deleted + inj.text[a:] == it.translation
        assert deleted.strip(), "something real was deleted"
        assert lab["source"] == "injector" and lab["type"] == "omission"
        kinds.add(lab["recipe"])
    assert "omission.sentence" in kinds


def test_omission_clause_kind_and_ineligible():
    hi = ST["hi"]
    two_clauses = hi[:-1] + ", " + hi  # punctuation-only splice of real output
    it = item("hi", translation=two_clauses)
    recipes = {inject.omission(it, Pool([it]), random.Random(s)).recipe for s in SEEDS}
    assert "omission.clause" in recipes
    assert inject.omission(item("hi"), Pool([]), random.Random(0)) is None  # one sentence, no clause


def test_addition_inserts_real_sentence_from_other_item():
    target = item("hi")
    # another item's real output (the Markdown fixture has no plain-prose sentence to splice)
    other = CleanItem("o-hi", "other", "hi", "x", ST["ta"] + " " + ST["bn"])
    pool = Pool([target, other, item("hi", "md")])
    for seed in SEEDS:
        inj = inject.addition(target, pool, random.Random(seed))
        a, b = inj.labels[0]["span"]
        frag = inj.text[a:b]
        assert frag in other.translation and frag not in target.translation
        assert inj.text[: a - 1] + inj.text[b:] == target.translation  # remove fragment + joining space
    assert inject.addition(target, Pool([target]), random.Random(0)) is None


def test_number_mutation_keeps_digit_script():
    it = item("bn")  # Bengali digits in the real output
    seen = set()
    for seed in SEEDS:
        inj = inject.number_unit(it, Pool([it]), random.Random(seed))
        lab = inj.labels[0]
        a, b = lab["span"]
        tok = inj.text[a:b]
        assert tok and all("BENGALI" in unicodedata.name(c) for c in tok)
        delta = len(inj.text) - len(it.translation)
        assert inj.text[:a] == it.translation[:a] and inj.text[b:] == it.translation[b - delta :]
        assert tok != it.translation[a : b - delta]
        seen.add(lab["recipe"])
    assert {"number_unit.change_digit", "number_unit.add_zero"} <= seen


def test_number_tail_unchanged_and_unit_swap():
    it = item("ta")  # Tamil output keeps "°C"
    recipes = set()
    for seed in SEEDS:
        inj = inject.number_unit(it, Pool([it]), random.Random(seed))
        a, b = inj.labels[0]["span"]
        delta = len(inj.text) - len(it.translation)
        assert inj.text[:a] == it.translation[:a]
        assert inj.text[b:] == it.translation[b - delta :]
        if inj.recipe == "number_unit.unit_swap":
            assert inj.text[a:b] == "°F"
        recipes.add(inj.recipe)
    assert "number_unit.unit_swap" in recipes


def test_number_not_eligible_without_source_number():
    it = item("hi", source="Water boils at sea level.")
    assert inject.number_unit(it, Pool([it]), random.Random(0)) is None


def test_english_entities_skips_sentence_starts():
    assert english_entities("The river Ganga flows past Varanasi. Ships sail to New York.") == ["Ganga", "Varanasi", "New York"]
    md = "## Boiling points\n\n- Adults drink more.\n\n| Liquid | Point |\n|---|---|\n| Water | 100 |\n\nIt is cold in Leh."
    assert english_entities(md) == ["Leh"]


def test_term_entity_replaces_with_real_entity_translation():
    md_hi = MT["hi"]
    newton_hi = md_hi[3 : md_hi.index(" ", 3)]  # the heading's first word = Bodhan's "Newton"
    other_word = ST["hi"].split()[0]  # a real Bodhan word used as another entity
    it = item("hi", "md", source="The second law of motion was stated by Newton in 1687.")
    pool = Pool([it], entity_translations={("Newton", "hi"): newton_hi, ("Kepler", "hi"): other_word})
    inj = inject.term_entity(it, pool, random.Random(1))
    assert inj is not None
    a, b = inj.labels[0]["span"]
    assert inj.text[a:b] == other_word
    assert inj.text[:a] + newton_hi + inj.text[b:] == md_hi
    assert "Newton" in inj.labels[0]["note"]
    assert inject.term_entity(it, Pool([it]), random.Random(1)) is None  # no entity translations


def test_format_edits_are_inside_the_labelled_line():
    code = "\n\n```python\nprint(1)\n```"  # ASCII code block appended to a real output
    it = item("hi", "md", translation=MT["hi"] + code)
    kinds = set()
    for seed in SEEDS:
        inj = inject.format_break(it, Pool([it]), random.Random(seed))
        a, b = inj.labels[0]["span"]
        line = inj.text[a:b]
        assert "\n" not in line and (a == 0 or inj.text[a - 1] == "\n") and (b == len(inj.text) or inj.text[b] == "\n")
        d = first_diff(it.translation, inj.text)
        assert a <= d <= b
        assert len(it.translation) - len(inj.text) == 1  # exactly one character deleted
        kinds.add(inj.recipe)
    assert kinds == {"format.math_dollar", "format.table_pipe", "format.code_fence", "format.link_target"}
    assert inject.format_break(item("hi"), Pool([]), random.Random(0)) is None


def test_wrong_language_uses_source_or_sibling():
    hi, ta, bn = item("hi"), item("ta"), item("bn")
    pool = Pool([hi, ta, bn])
    outs = set()
    for seed in SEEDS:
        inj = inject.wrong_language(hi, pool, random.Random(seed))
        assert inj.labels[0]["span"] == [0, len(inj.text)]
        assert inj.text in (hi.source, ta.translation, bn.translation)
        outs.add(inj.recipe)
    assert outs == {"wrong_language_script.untranslated", "wrong_language_script.other_language"}


def test_perturb_source_swaps_one_antonym_with_case():
    src = "Before noon the water level rises."
    pert, old, new = perturb_source(src, random.Random(3))
    assert (old, new) in {("Before", "After"), ("rises", "falls")}
    assert pert.replace(new, old, 1) == src
    assert perturb_source("Water boils at sea level.", random.Random(0)) is None


def _splice(tokens_from: str, idx: int, donor: str) -> str:
    toks = tokens_from.split(" ")
    toks[idx] = donor.split(" ")[0]
    return " ".join(toks)


def test_mistranslation_span_from_diff():
    hi = ST["hi"]
    new_hi = _splice(hi, 2, ST["bn"])  # real-output splice stands in for the re-translation
    it = item("hi", source="Water boils at a higher temperature at sea level.")
    calls = []

    async def fake_translate(text, lang):
        calls.append((text, lang))
        return new_hi

    inj = asyncio.run(mistranslation(it, fake_translate, random.Random(0)))
    assert calls == [("Water boils at a lower temperature at sea level.", "hi")]
    a, b = inj.labels[0]["span"]
    assert inj.text == new_hi and inj.text[a:b] == ST["bn"].split(" ")[0]
    assert inj.labels[0]["perturbed_source"] == calls[0][0]


def test_mistranslation_rejects_identical_or_rephrased():
    it = item("hi", source="The water is hot.")

    async def same(text, lang):
        return it.translation

    async def rephrased(text, lang):
        return ST["bn"]

    assert asyncio.run(mistranslation(it, same, random.Random(0))) is None
    assert asyncio.run(mistranslation(it, rephrased, random.Random(0))) is None
    assert changed_span("a b c", "a b c") is None


def speechlike(seconds=4.0, sr=16000):
    """Tone bursts separated by short gaps (word-like on/off structure)."""
    t = np.arange(int(seconds * sr)) / sr
    on = (np.floor(t / 0.4) % 1 == 0) & ((t % 0.4) < 0.3)
    return (0.3 * np.sin(2 * np.pi * 220 * t) * on).astype(np.float32), sr


def test_truncate_keeps_prefix_and_marks_cut_point():
    s, sr = speechlike()
    for seed in SEEDS:
        inj = inject.truncate(s, sr, random.Random(seed))
        (lab,) = inj.labels
        assert np.array_equal(inj.samples, s[: len(inj.samples)])
        assert lab["span"][0] == lab["span"][1] == pytest.approx(len(inj.samples) / sr, abs=1e-4)
        assert lab["span_unit"] == "seconds" and lab["field"] == "audio"
        assert 0.45 * 4 - 0.2 <= lab["span"][0] <= 0.8 * 4 + 0.2


def test_repeat_copy_matches_original_segment():
    s, sr = speechlike()
    for seed in SEEDS:
        inj = inject.repeat(s, sr, random.Random(seed))
        a_s, b_s = inj.labels[0]["span"]
        b, e = round(a_s * sr), round(b_s * sr)
        length = e - b
        assert np.array_equal(inj.samples[b:e], inj.samples[b - length : b])  # the copy repeats what precedes it
        assert np.array_equal(inj.samples[:b], s[:b]) and np.array_equal(inj.samples[e:], s[b:])


def test_silence_and_noise_spans():
    s, sr = speechlike()
    for seed in SEEDS:
        inj = inject.insert_silence(s, sr, random.Random(seed))
        a, b = (round(x * sr) for x in inj.labels[0]["span"])
        assert not inj.samples[a:b].any() and np.array_equal(inj.samples[:a], s[:a]) and np.array_equal(inj.samples[b:], s[a:])
        inj = inject.noise_burst(s, sr, random.Random(seed))
        a, b = (round(x * sr) for x in inj.labels[0]["span"])
        assert np.array_equal(inj.samples[:a], s[:a]) and np.array_equal(inj.samples[b:], s[b:])
        assert not np.array_equal(inj.samples[a:b], s[a:b])
        inj = inject.noise_bed(s, sr, random.Random(seed))
        assert inj.labels[0]["span"] == [0.0, round(len(s) / sr, 4)] and inj.labels[0]["severity"] == "minor"


def test_audio_recipes_ineligible_on_tiny_clip():
    tiny = np.zeros(1000, dtype=np.float32)
    for fn in (inject.truncate, inject.repeat, inject.insert_silence, inject.noise_burst, inject.noise_bed):
        assert fn(tiny, 16000, random.Random(0)) is None


def clean_items(tmp_path):
    items = []
    s, sr = speechlike(3.0)
    for lang in ("hi", "ta", "bn"):
        items.append(item(lang))
        if lang in MT:
            items.append(item(lang, "md"))
        for k in range(4):  # audio items reuse the real short translation
            wav = tmp_path / f"a{k}-{lang}.wav"
            wav.write_bytes(write_wav(np.roll(s, 800 * k), sr))
            items.append(CleanItem(f"aud{k}-{lang}", f"aud{k}", lang, SMOKE["source"], ST[lang], audio_path=str(wav)))
    return items


async def _fake_translate(text, lang):
    return ST[lang]


def build(tmp_path, seed):
    items = clean_items(tmp_path)
    return asyncio.run(build_labelled_set(items, Pool(items), seed=seed, translate=_fake_translate))


def test_build_balanced_and_valid(tmp_path):
    built = build(tmp_path, 7)
    injected = [b for b in built if b.labels]
    assert len(built) == 17 and 0.4 <= len(injected) / len(built) <= 0.6
    rows = []
    for k, b in enumerate(built):
        for lab in b.labels:
            assert lab["source"] == "injector" and lab["type"] in CATEGORIES
            if lab["span_unit"] == "chars":
                assert 0 <= lab["span"][0] <= lab["span"][1] <= len(b.translation)
            else:
                dur = len(b.samples) / b.sr
                assert 0 <= lab["span"][0] <= lab["span"][1] <= dur + 1e-3
        rows.append({"id": f"x{k}", "source": b.base.source, "lang": b.base.lang, "translation": b.translation, "labels": b.labels})
    ds = tmp_path / "built.jsonl"
    ds.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    loaded = load_dataset(ds)
    assert not loaded.bad and len(loaded.items) == len(rows)
    speech = [lab for it in loaded.items for lab in it.labels if lab.category.startswith("speech")]
    assert speech and all(lab.frame == "seconds" for lab in speech)


def test_build_is_reproducible(tmp_path):
    def sig(built):
        return [(b.base.id, b.translation, json.dumps(b.labels, sort_keys=True), None if b.samples is None else b.samples.tobytes()) for b in built]

    assert sig(build(tmp_path, 7)) == sig(build(tmp_path, 7))
    assert sig(build(tmp_path, 7)) != sig(build(tmp_path, 8))


def test_injector_never_imports_llm_client():
    inj_file = Path(__file__).parents[1] / "src" / "bodhan_eval" / "inject.py"
    assert inj_file.exists()
    tree = ast.parse(inj_file.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert "llm" not in mod and "openai" not in mod and "clients" not in mod, (inj_file.name, mod)
        elif isinstance(node, ast.Import):
            assert not any("openai" in a.name or "llm" in a.name for a in node.names), inj_file.name


def test_targets_met_scarcest_first_and_reproducible(tmp_path):
    items = clean_items(tmp_path)  # 5 text items (2 Markdown = only format-eligible ones), 12 audio items
    targets = {"format": 2, "wrong_language_script": 2, "speech_repetition": 3, "speech_garbled": 1}

    def go(seed):
        return asyncio.run(build_labelled_set(items, Pool(items), seed=seed, translate=_fake_translate, targets=targets))

    built = go(3)
    got = {}
    for b in built:
        for lab in b.labels:
            got[lab["type"]] = got.get(lab["type"], 0) + 1
    assert got == targets, "format (scarce) gets both Markdown items before wrong_language takes any"
    assert all(len(b.labels) <= 1 for b in built)
    sig = [(b.base.id, b.translation, json.dumps(b.labels, sort_keys=True)) for b in built]
    assert sig == [(b.base.id, b.translation, json.dumps(b.labels, sort_keys=True)) for b in go(3)]


# ==================== build_demo
ROOT = Path(__file__).parents[1]


def load_script():
    spec = importlib.util.spec_from_file_location("build_demo", ROOT / "scripts" / "build_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fake_clean_run(tmp: Path) -> tuple[Path, Path]:
    run = tmp / "clean_run"
    (run / "audio").mkdir(parents=True)
    inputs, records = [], []
    s, sr = speechlike(3.0)
    for lang in ("hi", "ta", "bn"):
        rows = [("smoke", SMOKE["source"], ST[lang], False)]
        if lang in MT:
            rows.append(("md", json.loads((ROOT / "tests/fixtures/bodhan_markdown.json").read_text(encoding="utf-8"))["source"], MT[lang], False))
        rows += [(f"aud{k}", SMOKE["source"], ST[lang], True) for k in range(4)]
        for k, (sid, src, tr, audio) in enumerate(rows):
            rid = f"{sid}-{lang}"
            inputs.append({"id": rid, "src_id": sid, "group": "x", "source": src, "lang": lang})
            gen = {"translation": tr}
            if audio:
                (run / "audio" / f"{rid}.wav").write_bytes(write_wav(np.roll(s, 500 * k), sr))
                gen["audio"] = f"audio/{rid}.wav"
            records.append({"id": rid, "lang": lang, "generated": gen})
    ci = tmp / "clean_input.jsonl"
    ci.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in inputs), encoding="utf-8")
    (run / "items.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")
    return ci, run


def test_inject_and_export_offline(tmp_path, monkeypatch):
    for k in ("BODHAN_TRANSLATE_KEY", "BODHAN_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)
    mod = load_script()
    ci, run = fake_clean_run(tmp_path)
    out = tmp_path / "sd" / "demo.jsonl"
    targets = "omission=1,addition=1,number_unit=1,format=1,wrong_language_script=1,speech_repetition=3,speech_garbled=3"
    args = [
        "inject",
        "--per-category",
        targets,
        "--clean-input",
        str(ci),
        "--clean-run",
        str(run),
        "--out",
        str(out),
        "--audio-dir",
        str(tmp_path / "sd" / "audio"),
        "--cache-dir",
        str(tmp_path / "cache"),
        "--offline",
    ]
    assert mod.main(args) == 0
    first = out.read_bytes()
    loaded = load_dataset(out)
    assert not loaded.bad and len(loaded.items) == 17
    assert all(it.audio is None or it.audio.exists() for it in loaded.items)
    assert 0.4 <= sum(1 for it in loaded.items if not it.labels) / 17 <= 0.6
    assert mod.main(args) == 0 and out.read_bytes() == first, "rebuild is deterministic"

    q_dir = tmp_path / "sd" / "review_queue"
    assert mod.main(["export-review", "--dataset", str(out), "--n", "8", "--out-dir", str(q_dir)]) == 0
    rows = [json.loads(x) for x in (q_dir / "queue.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 8 and all(not ({"labels", "meta", "annotator"} & set(r)) for r in rows)
    assert {r["lang"] for r in rows} == {"hi", "ta", "bn"}
    assert (q_dir / "REVIEW_QUEUE.md").exists()


def test_preexisting_labels_located_on_final_text():
    mod = load_script()
    text = MT["hi"]  # real output: heading line, inline code, ...
    issues = [
        {"base": "b", "type": "format", "severity": "minor", "locate": "line:0", "note": "n"},
        {"base": "b", "type": "format", "severity": "major", "locate": "code_inline:0", "note": "n"},
    ]
    labs = mod.preexisting_labels("b", text, issues)
    (a0, b0), (a1, b1) = labs[0]["span"], labs[1]["span"]
    assert a0 == 0 and text[b0] == "\n" and "\n" not in text[a0:b0]
    assert text[a1] == "`" and text[b1 - 1] == "`"
    assert all(lab["source"] == "reviewed_preexisting" for lab in labs)
    assert mod.preexisting_labels("other", text, issues) == []
