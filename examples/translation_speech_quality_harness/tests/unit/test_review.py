"""Review sheet (``python -m bodhan_eval.review``): export items for a human, import the answers as labels."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from bodhan_eval.dataset import apply_calibration_file, load_dataset
from bodhan_eval.review import COLUMNS, main

T_HI = "पानी 100 डिग्री पर उबलता है। भाप ऊपर उठती है।"


def flag(category: str, span: list[int] | None, text: str | None = None) -> dict:
    ev = [{"check": "numbers", "category": category, "score": 0.9, "detail": "Source number '100' changed"}]
    return {"category": category, "severity": "major", "confidence": 0.9, "span": span, "source_span": None,
            "text": text, "source_text": None, "evidence": ev, "checks": ["numbers"]}  # fmt: skip


def write(path: Path, rows: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


@pytest.fixture
def run_dir(tmp_path: Path) -> tuple[Path, Path]:
    """A finished run: 12 items in hi/ta, items 0-2 of each language flagged or queued."""
    rows, records = [], []
    for lang in ("hi", "ta"):
        for k in range(6):
            iid = f"x{k}"
            rows.append({"id": iid, "lang": lang, "source": "Water boils at 100 degrees. Steam rises.",
                         "translation": T_HI, "audio": None})  # fmt: skip
            records.append({"id": iid, "lang": lang, "status": "ok",
                            "flags": [flag("number", [5, 8], "100")] if k == 0 else [],
                            "uncertain": [flag("omission", None)] if k in (1, 2) else []})  # fmt: skip
    report = tmp_path / "run"
    report.mkdir()
    write(report / "items.jsonl", records)
    return report, write(report / "generated.jsonl", rows)


def export(run_dir, tmp_path: Path, *extra: str) -> list[dict]:
    report, dataset = run_dir
    out = tmp_path / "review.csv"
    args = ["export", "--report", str(report), "--dataset", str(dataset), "--out", str(out), "--no-back-translation"]
    assert main([*args, *extra]) == 0
    with out.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def test_export_puts_suspected_items_first_then_a_seeded_sample(run_dir, tmp_path: Path) -> None:
    rows = export(run_dir, tmp_path, "--per-lang", "4")
    assert list(rows[0].keys()) == COLUMNS
    for lang in ("hi", "ta"):
        mine = [r for r in rows if r["lang"] == lang]
        assert len(mine) == 4
        assert mine[0]["id"] == "x0" and {r["id"] for r in mine[1:3]} == {"x1", "x2"}  # flagged, then queue
    assert rows[0]["harness_flags"].startswith("number (major): '100'")
    assert rows[1]["harness_flags"] == "" and rows[1]["review_queue"].startswith("omission")
    assert rows[0]["suggested"] == "number" and rows[3]["suggested"] == "ok"
    assert all(r["your_verdict"] == "" for r in rows)


def test_suspected_items_are_a_seeded_sample_not_the_first_ids(tmp_path: Path) -> None:
    rows = [{"id": f"s{k:02d}", "lang": "hi", "source": "Water boils.", "translation": T_HI} for k in range(30)]
    records = [{"id": f"s{k:02d}", "lang": "hi", "status": "ok", "flags": [flag("number", None)], "uncertain": []}
               for k in range(30)]  # fmt: skip
    report = tmp_path / "run"
    report.mkdir()
    write(report / "items.jsonl", records)
    picked = export((report, write(tmp_path / "d.jsonl", rows)), tmp_path, "--per-lang", "6")
    ids = [r["id"] for r in picked]
    assert len(ids) == 6 and ids != sorted(ids)[:6] and ids != [f"s{k:02d}" for k in range(6)]
    again = export((report, tmp_path / "d.jsonl"), tmp_path, "--per-lang", "6", "--seed", "8")
    assert [r["id"] for r in again] != ids  # another seed, another sample


def test_export_is_deterministic_and_excel_friendly(run_dir, tmp_path: Path) -> None:
    export(run_dir, tmp_path, "--per-lang", "5")
    first = (tmp_path / "review.csv").read_bytes()
    export(run_dir, tmp_path, "--per-lang", "5")
    assert (tmp_path / "review.csv").read_bytes() == first
    assert first.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM: Excel shows Indian scripts correctly


def test_export_adds_the_back_translation_from_indic_translate(
    run_dir, tmp_path: Path, start_stub, monkeypatch
) -> None:
    stub = start_stub({"translate": {"en": {"पानी 100 डिग्री पर उबलता है।": "Water boils at 100 degrees.",
                                            "भाप ऊपर उठती है।": "Steam goes up."}}})  # fmt: skip
    monkeypatch.chdir(tmp_path)  # no .env from the repo
    monkeypatch.setenv("BODHAN_BASE_URL", stub.url)
    monkeypatch.setenv("BODHAN_API_KEY", "stub-key")
    report, dataset = run_dir
    out = tmp_path / "bt.csv"
    args = ["export", "--report", str(report), "--dataset", str(dataset), "--out", str(out), "--per-lang", "1"]
    assert main([*args, "--cache-dir", str(tmp_path / "cache")]) == 0
    with out.open(encoding="utf-8-sig", newline="") as fh:
        row = next(csv.DictReader(fh))
    assert row["back_translation"] == "Water boils at 100 degrees. Steam goes up."


def answer(rows: list[dict], path: Path, answers: dict[tuple[str, str], list[tuple[str, str, str]]]) -> Path:
    """Fill the sheet like a reviewer: (verdict, severity, error text) per row; a second error = a copied row."""
    out = []
    for r in rows:
        for verdict, sev, text in answers.get((r["id"], r["lang"]), [("", "", "")]):
            out.append({**r, "your_verdict": verdict, "your_severity": sev, "your_error_text": text})
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(out)
    return path


def test_import_turns_answers_into_saksham_labels(run_dir, tmp_path: Path) -> None:
    rows = export(run_dir, tmp_path, "--per-lang", "4")
    sheet = answer(rows, tmp_path / "answered.csv", {
        ("x0", "hi"): [("number", "major", "100")],
        ("x1", "hi"): [("ok", "", "")],
        ("x2", "hi"): [("omission", "", ""), ("Mistranslation", "critical", "भाप ऊपर")],
        ("x0", "ta"): [("garbled", "minor", "words not in the text")],
        ("x1", "ta"): [("spelling", "minor", "")],
    })  # fmt: skip
    out = tmp_path / "labels.jsonl"
    _, dataset = run_dir
    assert main(["import", "--csv", str(sheet), "--dataset", str(dataset), "--out", str(out)]) == 0
    lines = {(r["id"], r["lang"]): r["labels"] for r in map(json.loads, out.read_text("utf-8").splitlines())}
    assert lines[("x0", "hi")] == [{"type": "number", "severity": "major", "span": [5, 8], "span_on": "translation",
                                    "annotator": "saksham"}]  # fmt: skip
    assert lines[("x1", "hi")] == []  # reviewed and clean
    assert [lab["type"] for lab in lines[("x2", "hi")]] == ["omission", "mistranslation"]
    assert lines[("x2", "hi")][0]["span"] is None and lines[("x2", "hi")][0]["severity"] == "major"
    assert lines[("x2", "hi")][1]["span"] == [T_HI.index("भाप ऊपर"), T_HI.index("भाप ऊपर") + len("भाप ऊपर")]
    assert lines[("x0", "ta")][0]["type"] == "speech_garbled" and lines[("x0", "ta")][0]["span"] is None
    assert ("x1", "ta") not in lines and ("x2", "ta") not in lines  # unknown verdict / not answered: no labels
    summary = json.loads(out.with_suffix(".import.json").read_text("utf-8"))
    assert summary["unknown_verdicts"] == [{"id": "x1", "lang": "ta", "verdict": "spelling"}]
    assert summary["text_not_found"] == [{"id": "x0", "lang": "ta", "text": "words not in the text"}]
    # the labels file works as --calibration (shape 2: id + lang + labels)
    items, rep = load_dataset(dataset, None)
    apply_calibration_file(out, items, None, rep, "translation")
    by_key = {i.key: i for i in items}
    assert by_key["x0::hi"].labels and by_key["x0::hi"].labels[0].annotator == "saksham"
    assert by_key["x1::hi"].labels == [] and by_key["x1::ta"].labels is None
