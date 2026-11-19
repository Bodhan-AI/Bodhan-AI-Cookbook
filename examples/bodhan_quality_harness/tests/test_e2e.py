"""End-to-end tests: a 10-item dataset through the real CLI, offline.

Also asserts the determinism promise — a second run must produce a byte-identical
items.jsonl and the same digest.
"""

from __future__ import annotations

import json
from pathlib import Path

from bodhan_eval.cli import main

ITEMS = [
    {"id": "e-01", "source": "Water boils at 100 °C at sea level.",
     "lang": "hi", "translation": "समुद्र तल पर पानी 100 °C पर उबलता है।", "labels": []},
    {"id": "e-02", "source": "The heart pumps about 70 times per minute.",
     "lang": "hi", "translation": "हृदय प्रति मिनट लगभग 700 बार धड़कता है।",
     "labels": [{"type": "number", "severity": "critical", "span": [24, 27]}]},
    {"id": "e-03", "source": "Photosynthesis converts carbon dioxide into glucose.",
     "lang": "ta", "translation": "ஒளிச்சேர்க்கை கார்பன் டை ஆக்சைடை குளுக்கோஸாக மாற்றுகிறது.",
     "labels": []},
    {"id": "e-04", "source": "Jupiter is the largest planet in the solar system.",
     "lang": "ta", "translation": "Jupiter is the largest planet in the solar system.",
     "labels": [{"type": "language", "severity": "critical", "span": [0, 40]}]},
    {"id": "e-05", "source": "| Element | Symbol |\n| --- | --- |\n| Oxygen | O |",
     "lang": "bn", "translation": "| মৌল | প্রতীক |\n| --- | --- |\n| অক্সিজেন | O |",
     "labels": []},
    {"id": "e-06", "source": "| Element | Symbol |\n| --- | --- |\n| Oxygen | O |",
     "lang": "bn", "translation": "মৌল প্রতীক অক্সিজেন O",
     "labels": [{"type": "format", "severity": "major", "span": [0, 20]}]},
    {"id": "e-07", "source": "The area of a circle is $A = \\pi r^2$.",
     "lang": "hi", "translation": "वृत्त का क्षेत्रफल $A = \\pi r^2$ होता है।", "labels": []},
    {"id": "e-08", "source": "Newton's second law relates force, mass and acceleration.",
     "lang": "bn", "translation": "নিউটনের দ্বিতীয় সূত্র বল, ভর ও ত্বরণকে সম্পর্কিত করে।",
     "labels": []},
    {"id": "e-09", "source": "A leap year has 366 days.",
     "lang": "ta", "translation": "ஒரு நெட்டாண்டில் 366 நாட்கள் உள்ளன.", "labels": []},
    {"id": "e-10", "source": "Insulin regulates blood glucose levels.",
     "lang": "hi", "translation": "इंसुलिन रक्त शर्करा स्तर को नियंत्रित करता है।", "labels": []},
]


def _write_dataset(tmp_path: Path) -> Path:
    p = tmp_path / "data.jsonl"
    p.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in ITEMS) + "\n",
                 encoding="utf-8")
    return p


def test_cli_runs_and_writes_all_outputs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ds = _write_dataset(tmp_path)
    out = tmp_path / "report"

    code = main(["--dataset", str(ds), "--langs", "hi,ta,bn",
                 "--out", str(out), "--offline"])
    assert code == 0

    for name in ("summary.md", "summary.json", "items.jsonl"):
        assert (out / name).exists(), f"{name} missing"

    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["items_scored"] == 10
    assert summary["languages"] == ["bn", "hi", "ta"]

    items = [json.loads(l) for l in (out / "items.jsonl").read_text(encoding="utf-8").splitlines() if l]
    assert len(items) == 10

    # the changed digit in e-02 must be caught
    e02 = next(i for i in items if i["id"] == "e-02")
    assert any(f["category"] == "number" for f in e02["flags"])

    # the untranslated item e-04 must be caught
    e04 = next(i for i in items if i["id"] == "e-04")
    assert any(f["category"] == "language" for f in e04["flags"])

    # the clean table item e-05 must stay clean
    e05 = next(i for i in items if i["id"] == "e-05")
    assert e05["flag_count"] == 0


def test_rerun_is_byte_identical(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ds = _write_dataset(tmp_path)
    out1, out2 = tmp_path / "r1", tmp_path / "r2"

    assert main(["--dataset", str(ds), "--langs", "hi,ta,bn", "--out", str(out1), "--offline"]) == 0
    assert main(["--dataset", str(ds), "--langs", "hi,ta,bn", "--out", str(out2), "--offline"]) == 0

    assert (out1 / "items.jsonl").read_bytes() == (out2 / "items.jsonl").read_bytes()
    d1 = json.loads((out1 / "summary.json").read_text())["digest"]
    d2 = json.loads((out2 / "summary.json").read_text())["digest"]
    assert d1 == d2


def test_bad_items_are_skipped_not_fatal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "bad.jsonl"
    p.write_text(
        '{"id":"ok-1","source":"Hello world, this is a test.","lang":"hi",'
        '"translation":"यह एक परीक्षण है जो ठीक है।"}\n'
        'not json at all\n'
        '{"id":"ok-2","source":"Another test sentence here.","lang":"ta"}\n'
        '{"source":"missing id","lang":"hi"}\n',
        encoding="utf-8")
    out = tmp_path / "out"
    assert main(["--dataset", str(p), "--langs", "hi,ta", "--out", str(out), "--offline"]) == 0
    summary = json.loads((out / "summary.json").read_text())
    assert summary["items_scored"] == 2
    assert summary["items_skipped"] == 2
