"""End-to-end on a 10-item dataset with offline stub backends: run, report, rerun from cache, diff."""

import json

from conftest import FakeBodhan, FakeLLM

from bodhan_eval import cli

ITEMS = [
    ("c1", "Water boils at 100 °C at sea level.", "पानी समुद्र तल पर 100 °C पर उबलता है।", []),
    ("c2", "The Moon orbits the Earth in about 27 days.", "चंद्रमा लगभग 27 दिनों में पृथ्वी की परिक्रमा करता है।", []),
    ("c3", "Wash your hands for 20 seconds.", "अपने हाथ 20 सेकंड तक धोएं।", []),
    ("c4", "India has 28 states.", "भारत में 28 राज्य हैं।", []),
    ("c5", "Plants release oxygen.", "पौधे ऑक्सीजन छोड़ते हैं।", []),
    ("n1", "The human body has 206 bones.", "मानव शरीर में 208 हड्डियाँ होती हैं।", [{"type": "number", "span": [14, 17]}]),
    ("f1", "| a | b |\n|---|---|\n| 1 | 2 |", "| क | ख |\n|---|---|\n| 1  2 |", [{"type": "format", "span": [20, 28]}]),
    ("w1", "Newton wrote the laws of motion.", "न्यूटन ने laws of motion लिखे।", [{"type": "wrong_language", "span": [10, 24]}]),
    ("m1", "Ice floats because it is lighter than water.", "बर्फ तैरती है क्योंकि वह पानी से भारी है।", [{"type": "mistranslation", "span": [33, 37]}]),
    ("o1", "Boil the water. Then keep the clean container covered with a lid.", "पानी उबालें।", [{"type": "omission", "span": [16, 65]}]),
]


def _dataset(tmp_path, items=ITEMS):
    path = tmp_path / "data.jsonl"
    lines = [json.dumps({"id": i, "source": s, "lang": "hi", "translation": t, "labels": lab}, ensure_ascii=False) for i, s, t, lab in items]
    path.write_text("\n".join(lines + ["{not json", json.dumps({"id": "x", "lang": "hi"})]) + "\n", encoding="utf-8")
    return path


def _back(items=ITEMS) -> dict[str, str]:
    """Stub back-translation: the English source, except where the translation dropped content."""
    back = {t: s for _, s, t, _ in items}
    back["पानी उबालें।"] = "Boil the water."
    return back


def _patch(monkeypatch, bodhan):
    error = {"category": "mistranslation", "severity": "major", "translation_tokens": [7, 7], "explanation": "heavier vs lighter"}
    judge = FakeLLM({"[7]भारी": {"errors": [error]}})
    monkeypatch.setattr(cli, "Bodhan", lambda *a, **k: bodhan)
    monkeypatch.setattr(cli, "LLM", lambda *a, **k: judge)


def test_end_to_end_report_and_determinism(tmp_path, monkeypatch):
    bodhan = FakeBodhan(back=_back())
    _patch(monkeypatch, bodhan)
    data = _dataset(tmp_path)
    out = tmp_path / "r1"
    assert cli.main(["--dataset", str(data), "--langs", "hi", "--out", str(out), "--cache-dir", str(tmp_path / "c"), "--judge-repeats", "3"]) == 0

    for name in ("summary.md", "summary.json", "items.jsonl", "calibration.json", "run.json", "review_queue.csv"):
        assert (out / name).exists(), name
    items = {r["id"]: r for r in map(json.loads, (out / "items.jsonl").read_text(encoding="utf-8").splitlines())}
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["items_evaluated"] == 10 and summary["bad_items"] == 2

    cats = {k: {f["category"] for f in r["flags"]} for k, r in items.items()}
    assert "number" in cats["n1"] and "format" in cats["f1"] and "wrong_language" in cats["w1"]
    assert "mistranslation" in cats["m1"] and "omission" in cats["o1"]
    assert not any(cats[c] for c in ("c1", "c2", "c3", "c4", "c5"))
    flag = next(f for f in items["n1"]["flags"] if f["category"] == "number")
    assert items["n1"]["translation"][slice(*flag["span"])] == "208"

    cal = json.loads((out / "calibration.json").read_text(encoding="utf-8"))
    assert cal["detection_recall"] == 1.0 and cal["false_alarm_rate"] == 0.0

    # Rerun: identical report files.
    out2 = tmp_path / "r2"
    assert cli.main(["--dataset", str(data), "--langs", "hi", "--out", str(out2), "--cache-dir", str(tmp_path / "c"), "--judge-repeats", "3"]) == 0
    for name in ("summary.md", "summary.json", "items.jsonl", "calibration.json"):
        assert (out / name).read_bytes() == (out2 / name).read_bytes(), name


def test_baseline_diff_and_regression_gate(tmp_path, monkeypatch):
    worse = [(i, s, t.replace("27", "29").replace("20", "25"), lab) for i, s, t, lab in ITEMS[:5]]
    _patch(monkeypatch, FakeBodhan(back=_back(ITEMS[:5] + worse)))
    base = tmp_path / "base"
    cli.main(["--dataset", str(_dataset(tmp_path, ITEMS[:5])), "--langs", "hi", "--out", str(base), "--cache-dir", str(tmp_path / "c"), "--no-judge"])
    out = tmp_path / "now"
    code = cli.main(["--dataset", str(_dataset(tmp_path, worse)), "--langs", "hi", "--out", str(out), "--cache-dir", str(tmp_path / "c"),
                     "--no-judge", "--baseline", str(base), "--fail-on-regression", "0"])
    assert code == 3
    diff = (out / "diff.md").read_text(encoding="utf-8")
    assert "2 regressed" in diff and "c2" in diff and "c3" in diff


def test_dataset_with_utf8_bom_is_read(tmp_path, monkeypatch):
    _patch(monkeypatch, FakeBodhan(back=_back()))
    path = tmp_path / "bom.jsonl"
    path.write_text("﻿" + json.dumps({"id": "c1", "source": ITEMS[0][1], "lang": "hi", "translation": ITEMS[0][2]}, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    out = tmp_path / "o"
    assert cli.main(["--dataset", str(path), "--langs", "hi", "--out", str(out), "--cache-dir", str(tmp_path / "c"), "--no-judge"]) == 0
    assert json.loads((out / "summary.json").read_text(encoding="utf-8"))["bad_items"] == 0


def test_unknown_language_is_a_clean_error(tmp_path):
    assert cli.main(["--dataset", "missing.jsonl", "--langs", "xx", "--out", str(tmp_path / "o")]) == 2
