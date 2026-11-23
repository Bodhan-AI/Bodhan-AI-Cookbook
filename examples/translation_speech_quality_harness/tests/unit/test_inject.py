"""Injector (BUILD_PLAN P5): every text method, the per-item choice, the output file, and the never-the-LLM rule.

Method tests assert invariants that hold whatever the seeded RNG picks (several seeds per method): the edit is
where the label says, undoing it gives the original text back, and the label has the method's category and
severity. ``--with-models`` methods run against the stub server, whose Indic-Translate echoes unknown text,
so the re-translated sentence is the perturbed English itself and the label span is exactly the changed words.
Round-trip tests run the harness's own tier-0 checks on injected text: they must flag at the label.
"""

from __future__ import annotations

import ast
import json
import random
from pathlib import Path

import numpy as np
import pytest

from bodhan_eval import audio as A
from bodhan_eval import markup
from bodhan_eval.budget import Budget
from bodhan_eval.cache import Cache
from bodhan_eval.checks.deterministic import FormatCheck, ScriptCheck
from bodhan_eval.checks.roundtrip import NumberCheck, extract_numbers
from bodhan_eval.clients import BodhanClient, LLMClient
from bodhan_eval.config import Settings
from bodhan_eval.dataset import load_dataset
from bodhan_eval.inject import english, text
from bodhan_eval.inject.base import Edit, InjectContext
from bodhan_eval.inject.core import METHODS, item_rng, main
from bodhan_eval.models import Item
from bodhan_eval.normalize import segments, word_tokens
from bodhan_eval.spans import overlaps
from bodhan_eval.testing.stub_server import speech_like

SEEDS = range(6)

GANGA_EN = "The Ganga is 2,525 km long. It flows through five states, and it supports millions of farmers. Gravity was explained by Newton."
GANGA_HI = (
    "गंगा २,५२५ किलोमीटर लंबी है। यह पाँच राज्यों से होकर बहती है, और यह लाखों किसानों का सहारा है। गुरुत्वाकर्षण को Newton ने समझाया।"
)
GANGA_TA = "கங்கை 2,525 கிலோமீட்டர் நீளமானது. இது ஐந்து மாநிலங்கள் வழியாக பாய்கிறது, மேலும் இது கோடிக்கணக்கான விவசாயிகளுக்கு உதவுகிறது. ஈர்ப்பு விசையை Newton விளக்கினார்."
OTHER_EN = "The first electric motor was built by Faraday. Copper wires carry current over long distances."
OTHER_HI = "पहली विद्युत मोटर Faraday ने बनाई। तांबे के तार लंबी दूरी तक विद्युत धारा ले जाते हैं।"
MD_EN = (
    "## Planets\n\n| Planet | Moons |\n|---|---|\n| Earth | 1 |\n| Mars | 2 |\n\n"
    "The **largest** planet is Jupiter. Area is $A = \\pi r^2$ and speed is $v = \\frac{d}{t}$."
)
MD_HI = (
    "## ग्रह\n\n| ग्रह | चंद्रमा |\n|---|---|\n| पृथ्वी | 1 |\n| मंगल | 2 |\n\n"
    "सबसे **बड़ा** ग्रह बृहस्पति है। क्षेत्रफल $A = \\pi r^2$ है और गति $v = \\frac{d}{t}$ है।"
)


def it(source: str = GANGA_EN, translation: str = GANGA_HI, lang: str = "hi", id: str = "ganga", **kw) -> Item:
    return Item(id=id, lang=lang, source=source, translation=translation, **kw)


OTHER = it(OTHER_EN, OTHER_HI, id="faraday")


def apply(fn, item: Item, seed: int = 0, others: tuple[Item, ...] = (OTHER,), bodhan=None) -> Edit | None:
    ctx = InjectContext([item, *others], bodhan=bodhan)
    return fn(item, random.Random(seed), ctx)


def undo_insert(new: str, span: tuple[int, int]) -> str:
    """Remove inserted text and the one space that joined it."""
    a, b = span
    return (new[:a] + new[b:]).replace("  ", " ").replace(" \n", "\n").strip()


def removed(old: str, new: str, pos: int) -> str:
    """The stretch a deletion at ``pos`` took out (``new`` must be ``old`` minus one contiguous stretch)."""
    cut = len(old) - len(new)
    assert cut > 0 and new[:pos] == old[:pos] and old[pos + cut :] == new[pos:]
    return old[pos : pos + cut]


def bodhan_for(stub, tmp_path: Path) -> BodhanClient:
    settings = Settings(
        bodhan_base_url=stub.url,
        translate_key="stub-key",
        transliterate_key="stub-key",
        speak_key="stub-key",
        transcribe_key="stub-key",
        max_retries=1,
        request_timeout_s=10.0,
    )
    return BodhanClient(settings, Cache(tmp_path / "cache"), Budget(None))


# ---------------------------------------------------------------------------
# omission
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_drop_phrase_removes_two_or_three_words_without_digits(seed: int) -> None:
    e = apply(text.drop_phrase, it(), seed)
    assert e is not None and e.translation is not None
    a, b = e.span
    assert a == b and e.span_on == "translation"  # zero-width: where the words were
    gone = removed(GANGA_HI, e.translation, a)
    assert 2 <= len(word_tokens(gone)) <= 3
    assert not any(c.isdigit() for c in gone)


@pytest.mark.parametrize("seed", SEEDS)
def test_drop_clause_removes_a_comma_clause(seed: int) -> None:
    e = apply(text.drop_clause, it(), seed)
    assert e is not None and e.translation is not None
    gone = removed(GANGA_HI, e.translation, e.span[0])
    assert len(word_tokens(gone)) >= 3 and ", " in gone
    assert len(segments(e.translation)) == len(segments(GANGA_HI))  # the sentence itself survives


def test_drop_clause_ignores_digit_grouping_commas() -> None:
    assert apply(text.drop_clause, it("It is 2,525 km long.", "यह २,५२५ किलोमीटर लंबी है।")) is None


@pytest.mark.parametrize("seed", SEEDS)
def test_drop_sentence_labels_the_source_sentence_when_aligned(seed: int) -> None:
    e = apply(text.drop_sentence, it(), seed)
    assert e is not None and e.translation is not None
    assert len(segments(e.translation)) == len(segments(GANGA_HI)) - 1
    assert e.span_on == "source" and e.span in segments(GANGA_EN)


def test_drop_sentence_without_alignment_labels_the_gap_in_the_translation() -> None:
    e = apply(text.drop_sentence, it("One long English sentence about rivers and farms.", GANGA_HI))
    assert e is not None and e.span_on == "translation" and e.span[0] == e.span[1]


def test_drop_sentence_needs_two_sentences() -> None:
    assert apply(text.drop_sentence, it("Water boils.", "पानी उबलता है।")) is None


# ---------------------------------------------------------------------------
# addition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_repeat_sentence_inserts_a_shortened_copy(seed: int) -> None:
    e = apply(text.repeat_sentence, it(), seed)
    assert e is not None and e.translation is not None
    added = e.translation[e.span[0] : e.span[1]]
    sentences = [GANGA_HI[a:b] for a, b in segments(GANGA_HI)]
    assert added not in sentences and any(s.endswith(added) for s in sentences)  # varied: first word dropped
    assert undo_insert(e.translation, e.span) == GANGA_HI


@pytest.mark.parametrize("seed", SEEDS)
def test_foreign_sentence_comes_from_another_item_in_the_same_language(seed: int) -> None:
    tamil = it(GANGA_EN, GANGA_TA, lang="ta")
    e = apply(text.foreign_sentence, it(), seed, others=(OTHER, tamil))
    assert e is not None and e.translation is not None
    assert e.translation[e.span[0] : e.span[1]] in OTHER_HI
    assert undo_insert(e.translation, e.span) == GANGA_HI


def test_foreign_sentence_needs_another_item() -> None:
    assert apply(text.foreign_sentence, it(), others=()) is None


# ---------------------------------------------------------------------------
# mistranslation (programmatic)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", SEEDS)
def test_word_swap_puts_in_a_word_from_another_item(seed: int) -> None:
    e = apply(text.word_swap, it(), seed)
    assert e is not None and e.translation is not None
    new_word = e.translation[e.span[0] : e.span[1]]
    assert new_word in OTHER_HI and new_word not in GANGA_HI
    old_words = [t.text for t in word_tokens(GANGA_HI)]
    new_words = [t.text for t in word_tokens(e.translation)]
    assert len(old_words) == len(new_words) and sum(x != y for x, y in zip(old_words, new_words)) == 1


# ---------------------------------------------------------------------------
# number
# ---------------------------------------------------------------------------

NUM_EN = "1. Water is at 25 degrees and the density of H2O is 3.5. The river is 2,525 km long."
NUM_HI = "1. पानी २५ डिग्री पर है और H2O का घनत्व 3.5 है। नदी की लंबाई २,५२५ किलोमीटर है।"


def devanagari(s: str) -> bool:
    return any("०" <= c <= "९" for c in s)


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("fn", [text.first_digit, text.last_digit, text.decimal_shift], ids=lambda f: f.__name__)
def test_number_edits_change_one_number_and_keep_its_digit_system(fn, seed: int) -> None:
    e = apply(fn, it(NUM_EN, NUM_HI), seed)
    assert e is not None and e.translation is not None
    old, new = extract_numbers(NUM_HI), extract_numbers(e.translation)
    assert len(old) == len(new)
    changed = [k for k, (o, n) in enumerate(zip(old, new)) if o[0] != n[0]]
    assert len(changed) == 1
    k = changed[0]
    assert (new[k][1], new[k][2]) == e.span
    assert devanagari(NUM_HI[old[k][1] : old[k][2]]) == devanagari(e.translation[new[k][1] : new[k][2]])
    assert e.translation.startswith("1. ") and "H2O" in e.translation  # list markers and formulas untouched


def test_last_digit_needs_three_digits_and_decimal_shift_needs_a_decimal() -> None:
    small = it("It has 25 moons.", "इसके 25 चंद्रमा हैं।")
    assert apply(text.last_digit, small) is None
    assert apply(text.decimal_shift, small) is None


def test_decimal_shift_drops_the_point() -> None:
    e = apply(text.decimal_shift, it("Density is 3.5 here.", "घनत्व यहाँ 3.5 है।"))
    assert e is not None and e.translation == "घनत्व यहाँ 35 है।"
    assert e.translation[e.span[0] : e.span[1]] == "35"


# ---------------------------------------------------------------------------
# term
# ---------------------------------------------------------------------------


def test_entity_swap_replaces_a_latin_name_with_another_items_name() -> None:
    e = apply(text.entity_swap, it())
    assert e is not None and e.translation is not None
    assert e.translation[e.span[0] : e.span[1]] == "Faraday"
    assert e.translation.replace("Faraday", "Newton") == GANGA_HI


@pytest.mark.parametrize("seed", SEEDS)
def test_garble_entity_scrambles_the_rendering(seed: int) -> None:
    e = apply(text.garble_entity, it(), seed)
    assert e is not None and e.translation is not None
    garbled = e.translation[e.span[0] : e.span[1]]
    assert garbled != "Newton" and sorted(garbled) == sorted("Newton")


def test_term_methods_need_a_located_entity() -> None:
    plain = it("Water boils at sea level.", "पानी समुद्र तल पर उबलता है।")
    assert apply(text.entity_swap, plain) is None
    assert apply(text.garble_entity, plain) is None


def test_entity_found_through_its_transliteration_with_models(start_stub, tmp_path: Path) -> None:
    stub = start_stub({"transliterate": {"hi": {"Newton": "न्यूटन", "Faraday": "फैराडे"}}})
    item = it("Gravity was studied by Newton.", "गुरुत्वाकर्षण का अध्ययन न्यूटन ने किया।")
    other = it("A motor was built by Faraday.", "एक मोटर फैराडे ने बनाई।", id="f")
    e = apply(text.entity_swap, item, others=(other,), bodhan=bodhan_for(stub, tmp_path))
    assert e is not None and e.translation is not None
    assert e.translation[e.span[0] : e.span[1]] == "फैराडे"


# ---------------------------------------------------------------------------
# format
# ---------------------------------------------------------------------------

FORMAT_METHODS = [
    text.heading_marker,
    text.bold_marker,
    text.table_pipe,
    text.table_separator,
    text.math_dollar,
    text.latex_command,
]


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("fn", FORMAT_METHODS, ids=lambda f: f.__name__)
def test_format_edits_break_the_markup_they_target(fn, seed: int) -> None:
    e = apply(fn, it(MD_EN, MD_HI, id="md"), seed)
    assert e is not None and e.translation is not None
    new = e.translation
    kinds = [ln.kind for ln in markup.md_lines(new)]
    if fn is text.heading_marker:
        assert "heading2" not in kinds
    elif fn is text.bold_marker:
        assert markup.unmatched_marker_positions(new, "**")
    elif fn is text.table_pipe:
        cols = [ln.cols for ln in markup.md_lines(new) if ln.kind == "table"]
        assert min(cols) < max(cols)
    elif fn is text.table_separator:
        assert "table_sep" not in kinds
    elif fn is text.math_dollar:
        assert markup.unbalanced_dollars(new)
    else:
        assert [s.key for s in markup.math_segments(new)] != [s.key for s in markup.math_segments(MD_HI)]
    assert 0 <= e.span[0] < e.span[1] <= len(new)


@pytest.mark.parametrize("fn", FORMAT_METHODS, ids=lambda f: f.__name__)
def test_format_methods_skip_plain_prose(fn) -> None:
    assert apply(fn, it()) is None


@pytest.mark.parametrize("seed", range(3))
@pytest.mark.parametrize("fn", FORMAT_METHODS, ids=lambda f: f.__name__)
def test_format_check_catches_every_format_edit(fn, seed: int, make_ctx, run_one, fuse_item) -> None:
    """The loop in miniature: the harness's own format check flags the injected error where the label says.

    A deleted separator row is detected but not located (the check reports changed line counts with no span):
    that is a known gap of the check (K13), so this one row asserts detection only.
    """
    e = apply(fn, it(MD_EN, MD_HI, id="md"), seed)
    assert e is not None and e.translation is not None
    flags, _ = fuse_item(run_one(FormatCheck, make_ctx(), MD_EN, e.translation))
    hits = [f for f in flags if f.category == "format"]
    assert hits
    if fn is not text.table_separator:
        assert any(overlaps(f.span, e.span) for f in hits), [f.span for f in hits]


# ---------------------------------------------------------------------------
# wrong language
# ---------------------------------------------------------------------------


def test_source_english_replaces_the_whole_translation() -> None:
    e = apply(text.source_english, it())
    assert e is not None and e.translation == GANGA_EN and e.span == (0, len(GANGA_EN))


@pytest.mark.parametrize("seed", SEEDS)
def test_english_sentence_leaves_one_aligned_sentence_untranslated(seed: int) -> None:
    e = apply(text.english_sentence, it(), seed)
    assert e is not None and e.translation is not None
    assert e.translation[e.span[0] : e.span[1]] in [GANGA_EN[a:b] for a, b in segments(GANGA_EN)]
    assert len(segments(e.translation)) == len(segments(GANGA_HI))


def test_other_language_swaps_in_the_same_items_other_translation() -> None:
    tamil = it(GANGA_EN, GANGA_TA, lang="ta")
    e = apply(text.other_language, it(), others=(tamil,))
    assert e is not None and e.translation == GANGA_TA and e.span == (0, len(GANGA_TA))
    assert apply(text.other_language, it(), others=(OTHER,)) is None


@pytest.mark.parametrize("seed", range(3))
@pytest.mark.parametrize(
    ("fn", "check", "category"),
    [
        (text.source_english, ScriptCheck, "wrong_language"),
        (text.english_sentence, ScriptCheck, "wrong_language"),
        (text.first_digit, NumberCheck, "number"),
        (text.decimal_shift, NumberCheck, "number"),
    ],
    ids=lambda v: getattr(v, "__name__", v),
)
def test_tier0_checks_catch_wrong_language_and_number_edits(
    fn, check, category, seed: int, make_ctx, run_one, fuse_item
) -> None:
    src, hi = (NUM_EN, NUM_HI) if check is NumberCheck else (GANGA_EN, GANGA_HI)
    e = apply(fn, it(src, hi), seed)
    assert e is not None and e.translation is not None
    flags, _ = fuse_item(run_one(check, make_ctx(), src, e.translation))
    assert any(f.category == category and overlaps(f.span, e.span) for f in flags), [
        (f.category, f.span) for f in flags
    ]


# ---------------------------------------------------------------------------
# --with-models: perturb one English sentence, re-translate it, splice it in
# ---------------------------------------------------------------------------

MODEL_EN = (
    "Water expands when it freezes. Ice is lighter than water. It melts at 0 °C in 2 hours in March. "
    "All rivers flow above the plains before the rains."
)
MODEL_HI = (
    "पानी जमने पर फैलता है। बर्फ पानी से हल्की है। यह मार्च में 2 घंटे में 0 °C पर पिघलती है। "
    "सभी नदियाँ बारिश से पहले मैदानों के ऊपर बहती हैं।"
)


@pytest.mark.parametrize(
    ("fn", "expected"),
    [
        (english.negation, {"not"}),
        (english.antonym_swap, None),
        (english.relation_swap, {"below", "after"}),
        (english.quantifier_swap, None),
        (english.unit_swap, {"minutes", "°F"}),
        (english.month_swap, {"June"}),
        (english.extra_clause, None),
    ],
    ids=lambda v: getattr(v, "__name__", None),
)
def test_model_methods_splice_one_retranslated_sentence(fn, expected, start_stub, tmp_path: Path) -> None:
    e = apply(fn, it(MODEL_EN, MODEL_HI, id="m"), bodhan=bodhan_for(start_stub({}), tmp_path))
    assert e is not None and e.translation is not None
    changed = e.translation[e.span[0] : e.span[1]]
    assert changed.strip()
    if expected:
        assert changed in expected
    # exactly one translated sentence was replaced (the stub echoes, so that sentence is now the edited English)
    old, new = segments(MODEL_HI), segments(e.translation)
    assert len(old) == len(new)
    assert sum(MODEL_HI[a:b] != e.translation[c:d] for (a, b), (c, d) in zip(old, new)) == 1


def test_model_methods_need_aligned_sentences(start_stub, tmp_path: Path) -> None:
    merged = "पानी जमने पर फैलता है और बर्फ पानी से हल्की है।"  # two English sentences -> one Hindi sentence
    source = "Water expands when it freezes. Ice is lighter than water."
    assert apply(english.negation, it(source, merged), bodhan=bodhan_for(start_stub({}), tmp_path)) is None


# ---------------------------------------------------------------------------
# the per-item choice, the output file, and the rules
# ---------------------------------------------------------------------------


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


ROWS = [
    {"id": "ganga", "lang": "hi", "source": GANGA_EN, "translation": GANGA_HI},
    {"id": "ganga", "lang": "ta", "source": GANGA_EN, "translation": GANGA_TA},
    {"id": "faraday", "lang": "hi", "source": OTHER_EN, "translation": OTHER_HI},
    {"id": "md", "lang": "hi", "source": MD_EN, "translation": MD_HI},
    {"id": "num", "lang": "hi", "source": NUM_EN, "translation": NUM_HI},
]
POOL_METHODS = {"foreign_sentence", "word_swap", "entity_swap", "other_language"}  # these read other items


def run_cli(tmp_path: Path, rows: list[dict], *extra: str, name: str = "out") -> list[dict]:
    src = write_jsonl(tmp_path / f"{name}_in.jsonl", rows)
    out = tmp_path / f"{name}.jsonl"
    assert main(["--dataset", str(src), "--out", str(out), "--seed", "7", *extra]) == 0
    return [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]


def test_same_seed_gives_the_same_bytes(tmp_path: Path) -> None:
    for name in ("a", "b"):
        run_cli(tmp_path, ROWS, "--rate", "0.6", name=name)
    assert (tmp_path / "a.jsonl").read_bytes() == (tmp_path / "b.jsonl").read_bytes()
    assert (tmp_path / "a.inject.json").read_bytes() == (tmp_path / "b.inject.json").read_bytes()


@pytest.mark.parametrize("rate", ["0.5", "1"])
def test_an_items_fate_does_not_depend_on_the_other_items(tmp_path: Path, rate: str) -> None:
    full = run_cli(tmp_path, ROWS, "--rate", rate, name="full")
    part = run_cli(tmp_path, ROWS[1:], "--rate", rate, name="part")
    by_key = {(r["id"], r["lang"]): r for r in full}
    for r in part:
        f = by_key[(r["id"], r["lang"])]
        assert (f["labels"] == []) == (r["labels"] == [])  # clean or not: seed, id and lang only
        used = {lab["annotator"].split(":", 1)[1] for lab in f["labels"] + r["labels"]}
        if not used & POOL_METHODS:
            assert f == r


def test_item_rng_depends_on_seed_id_and_lang() -> None:
    a = item_rng(7, it()).random()
    assert a == item_rng(7, it()).random()
    assert a != item_rng(8, it()).random()
    assert a != item_rng(7, it(lang="ta")).random()
    assert a != item_rng(7, it(), "drop_phrase").random()


@pytest.mark.parametrize("fmt", ["csv", "jsonl"])
def test_skip_copies_listed_items_through_unlabelled(tmp_path: Path, fmt: str) -> None:
    """Items a human is reviewing stay exactly as generated, with no labels, until the review's labels arrive."""
    if fmt == "csv":
        skip = tmp_path / "review.csv"
        skip.write_text("﻿id,lang,source\nganga,hi,x\nmd,hi,y\n", encoding="utf-8")
    else:
        skip = write_jsonl(tmp_path / "skip.jsonl", [{"id": "ganga", "lang": "hi"}, {"id": "md", "lang": "hi"}])
    out = run_cli(tmp_path, ROWS, "--rate", "1", "--skip", str(skip))
    by_key = {(r["id"], r["lang"]): r for r in out}
    for key in (("ganga", "hi"), ("md", "hi")):
        assert by_key[key]["translation"] == next(r["translation"] for r in ROWS if (r["id"], r["lang"]) == key)
        assert "labels" not in by_key[key]
    assert by_key[("ganga", "ta")]["labels"]  # same id, other language: not skipped
    summary = json.loads((tmp_path / "out.inject.json").read_text(encoding="utf-8"))
    assert summary["skipped"] == 2 and summary["items"] == len(ROWS)


def test_rate_zero_labels_everything_clean(tmp_path: Path) -> None:
    out = run_cli(tmp_path, ROWS, "--rate", "0")
    assert all(r["labels"] == [] for r in out)
    assert [r["translation"] for r in out] == [r["translation"] for r in ROWS]


def test_rate_one_injects_one_labelled_error_per_item(tmp_path: Path) -> None:
    out = run_cli(tmp_path, ROWS, "--rate", "1")
    by_name = {m.name: m for m in METHODS}
    for r in out:
        assert len(r["labels"]) == 1
        lab = r["labels"][0]
        assert lab["annotator"].startswith("injector:")
        m = by_name[lab["annotator"].split(":", 1)[1]]
        assert (m.category, m.severity) == (lab["type"], lab["severity"])
        whole = r["source"] if lab["span_on"] == "source" else r["translation"]
        a, b = lab["span"]
        assert 0 <= a <= b <= len(whole) and (a == b or whole[a:b].strip())


def test_output_loads_with_canonical_labels(tmp_path: Path) -> None:
    run_cli(tmp_path, ROWS, "--rate", "1")
    items, rep = load_dataset(tmp_path / "out.jsonl", None)
    assert not rep.bad and not rep.unknown_label_types and len(items) == len(ROWS)
    assert all(i.labels and i.labels[0].category and i.labels[0].annotator for i in items)


def test_categories_filter_and_summary(tmp_path: Path) -> None:
    out = run_cli(tmp_path, ROWS, "--rate", "1", "--categories", "format,numbers")
    cats = {lab["type"] for r in out for lab in r["labels"]}
    assert cats and cats <= {"format", "number"}
    summary = json.loads((tmp_path / "out.inject.json").read_text(encoding="utf-8"))
    assert summary["items"] == len(ROWS)
    assert sum(summary["per_category"].values()) == summary["injected"]
    assert summary["clean"] + summary["injected"] + summary["passed_through"] + summary["not_injectable"] == len(ROWS)


def test_items_already_labelled_pass_through_untouched(tmp_path: Path) -> None:
    human = {"type": "mistranslation", "severity": "major", "span": [0, 4], "annotator": "saksham"}
    rows = [{**ROWS[0], "labels": [human]}, *ROWS[1:]]
    out = run_cli(tmp_path, rows, "--rate", "1")
    assert out[0]["translation"] == GANGA_HI
    assert out[0]["labels"] == [{**human, "span_on": "translation"}]


def test_unknown_category_is_refused(tmp_path: Path) -> None:
    src = write_jsonl(tmp_path / "in.jsonl", ROWS)
    with pytest.raises(SystemExit):
        main(["--dataset", str(src), "--out", str(tmp_path / "o.jsonl"), "--categories", "spelling"])


def test_translation_edit_drops_audio_that_no_longer_matches(tmp_path: Path) -> None:
    wav = tmp_path / "ganga.hi.wav"
    A.write_wav(wav, speech_like(4.0), 24000)
    rows = [{**ROWS[0], "audio": wav.name}]
    out = run_cli(tmp_path, rows, "--rate", "1", "--categories", "omission,number,wrong_language")
    assert out[0]["labels"] and out[0]["audio"] is None
    summary = json.loads((tmp_path / "out.inject.json").read_text(encoding="utf-8"))
    assert summary["audio_dropped"] == 1


def test_clean_items_keep_their_audio_path_relative_to_the_output(tmp_path: Path) -> None:
    (tmp_path / "audio").mkdir()
    wav = tmp_path / "audio" / "ganga.hi.wav"
    A.write_wav(wav, speech_like(4.0), 24000)
    rows = [{**ROWS[0], "audio": "audio/ganga.hi.wav"}]
    (tmp_path / "sub").mkdir()
    src = write_jsonl(tmp_path / "in.jsonl", rows)
    assert main(["--dataset", str(src), "--out", str(tmp_path / "sub" / "o.jsonl"), "--rate", "0"]) == 0
    row = json.loads((tmp_path / "sub" / "o.jsonl").read_text(encoding="utf-8"))
    assert row["audio"] == "../audio/ganga.hi.wav"


def test_with_models_needs_the_translate_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("BODHAN_API_KEY", "BODHAN_TRANSLATE_KEY"):
        monkeypatch.setenv(var, "")
    src = write_jsonl(tmp_path / "in.jsonl", ROWS)
    assert main(["--dataset", str(src), "--out", str(tmp_path / "o.jsonl"), "--with-models"]) == 1


def test_with_models_resynthesises_audio_and_never_touches_the_llm(
    start_stub, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TESTING §7: the injector runs with an LLM client that raises on any use."""

    def boom(*a, **k):
        raise AssertionError("the injector must never call the LLM")

    monkeypatch.setattr(LLMClient, "__init__", boom)
    monkeypatch.setattr(LLMClient, "chat_json", boom)
    stub = start_stub({})
    monkeypatch.setenv("BODHAN_BASE_URL", stub.url)
    monkeypatch.setenv("BODHAN_API_KEY", "stub-key")
    for var in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.setenv(var, "set-but-unused")
    wav = tmp_path / "w.wav"
    A.write_wav(wav, speech_like(5.0), 24000)
    rows = [{"id": "m", "lang": "hi", "source": MODEL_EN, "translation": MODEL_HI, "audio": wav.name}]
    out = run_cli(
        tmp_path, rows, "--rate", "1", "--categories", "mistranslation", "--with-models",
        "--audio-out", str(tmp_path / "audio"), "--cache-dir", str(tmp_path / "cache"),
    )  # fmt: skip
    lab = out[0]["labels"][0]
    assert lab["type"] == "mistranslation" and lab["annotator"] != "injector:word_swap"
    assert out[0]["audio"] and (tmp_path / out[0]["audio"]).is_file()
    x, _ = A.load_audio(tmp_path / out[0]["audio"])
    assert len(x) > 0 and not np.array_equal(x, A.load_audio(wav)[0])
    assert all("chat/completions" not in q["path"] for q in stub.requests)


def test_injector_source_never_imports_the_llm_client() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "bodhan_eval" / "inject"
    files = list(root.glob("*.py"))
    assert files
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert "LLMClient" not in names and "openai" not in names, path.name
        assert not any("judge" in m or "openai" in m for m in mods), path.name
