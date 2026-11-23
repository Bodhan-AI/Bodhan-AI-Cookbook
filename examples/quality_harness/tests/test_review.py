import csv
import json

import pytest

from bodhan_eval.review import export, import_answers


def cand(check, category, span, **evidence):
    return {"check": check, "category": category, "side": "translation", "span": span, "text": "x",
            "evidence": evidence, "standalone": True}


def test_export_fill_import_round_trip(tmp_path):
    report = tmp_path / "report"
    report.mkdir()
    records = [
        {"id": "a", "lang": "hi", "source": "575 CE", "translation": "570 ई.", "back_translation": "570 CE",
         "candidates": [cand("numbers", "number", [0, 3], kind="changed", source="575", translation="570"),
                        cand("entities", "term", [4, 6], entity="CE")]},
        {"id": "b", "lang": "hi", "source": "Hi.", "translation": "नमस्ते।", "candidates": []},
        {"id": "c", "lang": "hi", "source": "Bye.", "translation": None, "candidates": []},  # unscored: never exported
        {"id": "a", "lang": "ta", "source": "575 CE", "translation": "575", "candidates": [cand("format", "format", [0, 1])]},
    ]
    (report / "items.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    sheet = tmp_path / "review.csv"
    assert export(report, "hi", 5, sheet) == 2

    rows = list(csv.reader(sheet.open(encoding="utf-8-sig")))
    assert sheet.read_bytes().startswith(b"\xef\xbb\xbf") and rows[0][:3] == ["id", "lang", "row"]
    body = [r for r in rows[1:] if not r[0].startswith("#")]
    assert [(r[0], r[2]) for r in body] == [("a", "item"), ("a", "0"), ("a", "1"), ("b", "item")]
    assert body[1][6] == "source says 575, translation says 570"

    # The reviewer: number is real, the name is a false alarm, and b has an error the harness missed.
    body[1][7:10] = ["real", "major", "575 → 570"]
    body[2][7] = "false"
    body[3][4], body[3][8], body[3][9] = "fluency", "minor", "awkward greeting"
    with sheet.open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows([rows[0], *body])

    labels = tmp_path / "labels.jsonl"
    labels.write_text(json.dumps({"id": "z", "lang": "hi", "labels": [], "unsure": []}) + "\n", encoding="utf-8")
    stats = import_answers(sheet, labels, report)
    assert stats == {"items": 2, "clean": 0, "real_from_candidates": 1, "missed": 1, "unsure": 0}
    out = {(e["id"], e["lang"]): e for e in map(json.loads, labels.read_text(encoding="utf-8").splitlines())}
    assert ("z", "hi") in out  # earlier answers for other items are kept
    (real,) = out[("a", "hi")]["labels"]
    assert real["type"] == "number" and real["span"] == [0, 3] and real["annotator"] == "candidate-manual"
    assert out[("b", "hi")]["labels"][0]["type"] == "fluency" and "span" not in out[("b", "hi")]["labels"][0]


def test_import_rejects_incomplete_answers(tmp_path):
    report = tmp_path / "report"
    report.mkdir()
    (report / "items.jsonl").write_text(json.dumps(
        {"id": "a", "lang": "hi", "source": "s", "translation": "t", "candidates": [cand("format", "format", [0, 1])]}) + "\n")
    sheet = tmp_path / "s.csv"
    sheet.write_text("id,lang,row,check,category,text,why,verdict,severity,note\na,hi,0,format,format,,,real,,\n")
    with pytest.raises(SystemExit, match="needs a category and a severity"):
        import_answers(sheet, tmp_path / "l.jsonl", report)
