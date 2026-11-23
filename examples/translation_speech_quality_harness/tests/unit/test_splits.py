"""Splitting the labelled demo data (``python -m bodhan_eval.splits``, DATASET_PLAN step 5)."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from bodhan_eval.splits import main

CATS = ["omission", "addition", "mistranslation", "number", "term", "format", "wrong_language", "speech_omission"]


def lab(category: str, annotator: str = "injector:x") -> dict:
    return {"type": category, "severity": "major", "span": None, "span_on": "translation", "annotator": annotator}


def write(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


def read(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def gold_rows() -> list[dict]:
    """3 languages x 40 items: 20 clean, 16 with one error (8 categories x 2), 4 unlabelled (on a review sheet)."""
    rows = []
    for lang in ("hi", "ta", "bn"):
        for k in range(40):
            row = {"id": f"t{k:02d}", "lang": lang, "source": f"Text {k}.", "translation": f"पाठ {k}।",
                   "audio": f"../outputs/demo_audio/t{k:02d}.{lang}.wav"}  # fmt: skip
            if k < 20:
                row["labels"] = []
            elif k < 36:
                row["labels"] = [lab(CATS[(k - 20) % 8])]
            rows.append(row)
    return rows


def heldout_rows() -> list[dict]:
    return [
        {"id": f"h{k:02d}", "lang": lang, "source": f"Text {k}.", "translation": f"టెక్స్ట్ {k}.",
         "labels": [] if k % 2 else [lab(CATS[k % 7])]}
        for lang in ("te", "mr") for k in range(12)
    ]  # fmt: skip


def run(tmp_path: Path, *extra: str) -> Path:
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    write(data / "demo_gold.jsonl", gold_rows())
    write(data / "heldout_gold.jsonl", heldout_rows())
    args = ["--gold", str(data / "demo_gold.jsonl"), "--heldout-gold", str(data / "heldout_gold.jsonl"),
            "--out-dir", str(data), "--seed", "7"]  # fmt: skip
    assert main([*args, *extra]) == 0
    return data


def test_the_dataset_the_harness_sees_has_no_labels(tmp_path: Path) -> None:
    data = run(tmp_path)
    demo, gold = read(data / "demo.jsonl"), read(data / "demo_gold.jsonl")
    assert [(r["id"], r["lang"]) for r in demo] == [(r["id"], r["lang"]) for r in gold]
    assert all("labels" not in r for r in demo)
    assert demo[0]["audio"] == gold[0]["audio"] and demo[0]["source"] == gold[0]["source"]


def test_calibration_subset_is_balanced(tmp_path: Path) -> None:
    data = run(tmp_path)
    cal = read(data / "demo_labels.jsonl")
    gold = {(r["id"], r["lang"]): r for r in read(data / "demo_gold.jsonl")}
    assert len(cal) == 40 and len({(r["id"], r["lang"]) for r in cal}) == 40
    assert all(set(r) == {"id", "lang", "labels"} and r["labels"] == gold[(r["id"], r["lang"])]["labels"] for r in cal)
    assert sum(1 for r in cal if not r["labels"]) == 20  # half clean
    assert Counter(r["lang"] for r in cal) == {"hi": 14, "ta": 13, "bn": 13} or max(
        Counter(r["lang"] for r in cal).values()
    ) - min(Counter(r["lang"] for r in cal).values()) <= 2
    assert {r["labels"][0]["type"] for r in cal if r["labels"]} == set(CATS)  # every category, speech included


def test_bakeoff_set_has_text_errors_of_every_category_and_the_heldout_language(tmp_path: Path) -> None:
    data = run(tmp_path, "--bakeoff-size", "40", "--bakeoff-heldout", "10", "--bakeoff-heldout-lang", "te")
    bake = read(data / "bakeoff.jsonl")
    main_part = [r for r in bake if r["lang"] in ("hi", "ta", "bn")]
    held = [r for r in bake if r["lang"] == "te"]
    assert len(main_part) == 40 and len(held) == 10 and {r["lang"] for r in bake} == {"hi", "ta", "bn", "te"}
    assert sum(1 for r in main_part if not r["labels"]) == 20 and sum(1 for r in held if not r["labels"]) == 5
    types = {lab["type"] for r in bake for lab in r["labels"]}
    assert "speech_omission" not in types  # the judge reads text only
    assert set(CATS[:7]) <= types
    assert all(r["labels"] is not None for r in bake)


def test_extra_labels_label_the_reviewed_items_and_are_copied_not_edited(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    extra = write(tmp_path / "saksham.jsonl", [
        {"id": "t36", "lang": "hi", "labels": [lab("mistranslation", "saksham")]},
        {"id": "t37", "lang": "ta", "labels": []},
    ])  # fmt: skip
    before = extra.read_bytes()
    run(tmp_path, "--extra-labels", str(extra), "--bakeoff-size", "60")
    bake = {(r["id"], r["lang"]): r for r in read(data / "bakeoff.jsonl")}
    assert bake[("t36", "hi")]["labels"][0]["annotator"] == "saksham"
    assert ("t37", "ta") in bake and bake[("t37", "ta")]["labels"] == []
    assert ("t38", "hi") not in bake  # still unlabelled: never in a labelled set
    assert extra.read_bytes() == before
    gold = {(r["id"], r["lang"]): r for r in read(data / "demo_gold.jsonl")}
    assert "labels" not in gold[("t36", "hi")]  # the gold file is input, never rewritten


def test_the_rest_of_the_bakeoff_complements_the_reviewed_items(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    extra = write(tmp_path / "saksham.jsonl", [
        {"id": f"t{k}", "lang": lang, "labels": [lab("mistranslation", "saksham")]}
        for k, lang in ((36, "hi"), (37, "ta"), (38, "bn"))
    ])  # fmt: skip
    run(tmp_path, "--extra-labels", str(extra), "--bakeoff-size", "10", "--bakeoff-heldout", "0")
    bake = read(data / "bakeoff.jsonl")
    assert len(bake) == 10 and sum(1 for r in bake if not r["labels"]) == 5  # still half clean overall
    filled = [r["labels"][0]["type"] for r in bake if r["labels"] and r["labels"][0]["annotator"] != "saksham"]
    assert len(filled) == 2 and "mistranslation" not in filled  # categories the review did not cover come first


def verdict(iid: str, lang: str, category: str, text: str | None, v: str, span: list[int] | None = None) -> dict:
    return {"id": iid, "lang": lang, "category": category, "severity": "major", "span": span, "text": text,
            "source_span": None if text else [0, 4], "verdict": v, "reviewer": "ai-review"}  # fmt: skip


def test_known_errors_from_a_review_of_the_generated_outputs_join_the_gold(tmp_path: Path) -> None:
    review = write(tmp_path / "review.jsonl", [
        verdict("t00", "hi", "term", "पाठ", "real", [0, 3]),  # a clean item that was not clean
        verdict("t01", "hi", "omission", "पाठ", "debatable", [0, 3]),  # unsure: neither clean nor labelled
        verdict("t02", "hi", "term", "पाठ", "false_alarm", [0, 3]),  # stays clean
        verdict("t20", "hi", "term", "20।", "real", [9, 12]),  # injected item: found again in its translation
        verdict("t21", "hi", "number", "not in the text", "real", [0, 5]),  # injected text replaced it: dropped
        verdict("t22", "hi", "omission", None, "real"),  # source-side label: kept as is
        verdict("t36", "hi", "speech_omission", "पाठ", "real", [0, 3]),  # review-sheet item: now labelled
    ])  # fmt: skip
    data = run(tmp_path, "--known-errors", str(review))
    gold = {(r["id"], r["lang"]): r for r in read(data / "demo_gold_reviewed.jsonl")}
    assert gold[("t00", "hi")]["labels"] == [{"type": "term", "severity": "major", "span": [0, 3],
                                              "span_on": "translation", "annotator": "ai-review"}]  # fmt: skip
    assert "labels" not in gold[("t01", "hi")] and gold[("t02", "hi")]["labels"] == []
    t20 = gold[("t20", "hi")]
    assert [lab["annotator"] for lab in t20["labels"]] == ["injector:x", "ai-review"]
    assert t20["labels"][1]["span"] == [4, 7] and t20["translation"][4:7] == "20।"
    assert [lab["annotator"] for lab in gold[("t21", "hi")]["labels"]] == ["injector:x"]
    assert gold[("t22", "hi")]["labels"][1] == {"type": "omission", "severity": "major", "span": [0, 4],
                                                 "span_on": "source", "annotator": "ai-review"}  # fmt: skip
    assert gold[("t36", "hi")]["labels"][0]["type"] == "speech_omission"
    cal = {(r["id"], r["lang"]) for r in read(data / "demo_labels.jsonl")}
    assert ("t01", "hi") not in cal  # never used as clean
    summary = json.loads((data / "splits.json").read_text("utf-8"))
    assert summary["known_errors"] == {"added": 4, "dropped": 1, "unsure_items": 1}


def test_split_is_deterministic_and_seeded(tmp_path: Path) -> None:
    data = run(tmp_path)
    first = (data / "demo_labels.jsonl").read_bytes(), (data / "bakeoff.jsonl").read_bytes()
    run(tmp_path)
    assert ((data / "demo_labels.jsonl").read_bytes(), (data / "bakeoff.jsonl").read_bytes()) == first
    run(tmp_path, "--seed", "8")
    assert (data / "demo_labels.jsonl").read_bytes() != first[0]
    summary = json.loads((data / "splits.json").read_text("utf-8"))
    assert summary["calibration"]["items"] == 40 and summary["demo"]["items"] == 120
    assert summary["demo"]["clean"] == 60 and summary["demo"]["unlabelled"] == 12  # unlabelled is not clean
