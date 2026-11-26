import json

import pytest
from conftest import FakeBackend

from bodhan_eval.cache import Cache
from bodhan_eval.calibrate import evaluate
from bodhan_eval.checks_text import format_signal, number_signal
from bodhan_eval.cli import main
from bodhan_eval.cost import BudgetExceeded
from bodhan_eval.injector import TEXT_KINDS, inject_dataset

HI = [
    ("पानी समुद्र तल पर 100 डिग्री पर उबलता है। यह एक सामान्य तथ्य है।", "Water boils at 100 degrees at sea level. This is a common fact."),
    ("सूर्य हर सुबह पूर्व से उगता है। किसान 25 खेतों में गेहूं उगाते हैं।", "The sun rises in the east every morning. Farmers grow wheat in 25 fields."),
    ("# परिचय\n\n| नाम | मान |\n| --- | --- |\n| क | 1 |\n\nसमीकरण $x^2 + 1$ है।", "# Intro\n\n| name | value |\n| --- | --- |\n| a | 1 |\n\nThe equation is $x^2 + 1$."),
]


def items():
    out = []
    for k in range(12):
        tr, src = HI[k % 3]
        out.append({"id": f"i{k}", "lang": "hi", "source": src, "translation": tr})
    return out


# ---- injector
@pytest.mark.parametrize("kind", TEXT_KINDS)
def test_injector_changes_text_and_labels(kind):
    its = items()
    out = inject_dataset(its, rate=1.0, seed=3, kinds=[kind])
    changed = [(a, b) for a, b in zip(its, out) if b["translation"] != a["translation"]]
    assert changed, kind
    for _a, b in changed:
        assert b["labels"][0]["type"] == kind and b["labels"][0]["label_source"] == "injector"


def test_injector_is_deterministic_and_number_labels_point_at_source():
    a = inject_dataset(items(), rate=0.7, seed=5)
    b = inject_dataset(items(), rate=0.7, seed=5)
    assert a == b
    out = inject_dataset(items(), rate=1.0, seed=1, kinds=["number"])
    for o in out:
        if o["labels"]:
            lab = o["labels"][0]
            s, e = lab["span"]
            assert o["source"][s:e].isdigit()
            assert number_signal(o["source"], o["translation"])["changed"]


def test_injected_format_breaks_detected():
    out = inject_dataset([it for it in items() if "|" in it["translation"]], rate=1.0, seed=1, kinds=["format"])
    for o in out:
        if o["labels"]:
            assert format_signal(o["source"], o["translation"])


# ---- cache + budget
def test_cache_key_exactness_and_zero_cost_rerun(make_services):
    be = FakeBackend()
    svc = make_services(be)
    svc.translate("abc", "en", "hi"); svc.translate("abc", "en", "hi"); svc.translate("abd", "en", "hi")
    assert be.calls["translate"] == 2
    assert svc.cost.calls["translate"] == 3


def test_budget_blocks_new_spend_but_not_cache(make_services, tmp_path):
    svc = make_services(FakeBackend(), max_inr=0.0)
    with pytest.raises(BudgetExceeded):
        svc.speak("hello world", "hi")
    svc2 = make_services(FakeBackend(), max_inr=None)
    svc2.speak("hello world", "hi")
    svc3 = make_services(FakeBackend(), max_inr=0.0)  # same cache dir, zero budget
    assert svc3.speak("hello world", "hi")


def test_corrupt_cache_entry_is_a_miss(tmp_path):
    c = Cache(tmp_path / "c")
    c.put("k", {"a": 1}, "v")
    for p in (tmp_path / "c").rglob("*.json"):
        p.write_text("{not json")
    assert c.get("k", {"a": 1}) is None


# ---- evaluate()
def test_evaluate_metrics():
    res = [
        {"status": "ok", "labels": [{"category": "number", "span": [0, 3]}], "flags": [{"category": "number", "span_source": [1, 2], "span_translation": None}]},
        {"status": "ok", "labels": [{"category": "omission", "span": None}], "flags": []},
        {"status": "ok", "labels": [], "flags": [{"category": "format", "span_source": None, "span_translation": None}]},
        {"status": "ok", "labels": [], "flags": []},
    ]
    m = evaluate(res)
    assert m["detection_recall"] == 0.5 and m["false_alarm_rate"] == 0.5
    assert m["localization_rate"] == 1.0 and m["category_accuracy"] == 1.0  # only detected errors count
    assert m["per_category"]["number"]["precision"] == 1.0


# ---- end to end: 10+ items, bad lines, calibration, rerun determinism, baseline diff
def write_dataset(path, errors=True):
    its = inject_dataset(items(), rate=0.5, seed=2) if errors else items()
    lines = [json.dumps(it, ensure_ascii=False) for it in its]
    lines.insert(3, "{this is not json")
    lines.insert(5, json.dumps({"id": "bad", "source": "", "lang": "hi"}))
    path.write_text("\n".join(lines), encoding="utf-8")


def test_end_to_end(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ds = tmp_path / "d.jsonl"
    write_dataset(ds)
    be = FakeBackend()
    args = ["--dataset", str(ds), "--langs", "hi", "--out", str(tmp_path / "r1"), "--cache-dir", str(tmp_path / "cc"),
            "--concurrency", "2", "--judge-runs", "2"]
    assert main(args, backend=be) == 0
    r1 = tmp_path / "r1"
    for f in ("summary.md", "summary.json", "items.jsonl", "calibration.json"):
        assert (r1 / f).exists()
    s = json.loads((r1 / "summary.json").read_text(encoding="utf-8"))
    assert s["items_malformed_skipped"] == 2 and s["items_evaluated"] == 12
    assert s["calibration_metrics"]["n_labelled_items"] == 12
    calls_after_first = dict(be.calls)

    # rerun from cache: identical report, no new model calls
    assert main(args[:5] + [str(tmp_path / "r2")] + args[6:], backend=be) == 0
    assert be.calls == calls_after_first
    for f in ("summary.md", "summary.json", "items.jsonl", "calibration.json"):
        assert (r1 / f).read_bytes() == (tmp_path / "r2" / f).read_bytes(), f

    # baseline diff
    assert main(args[:5] + [str(tmp_path / "r3"), "--baseline", str(r1)] + args[6:], backend=be) == 0
    assert (tmp_path / "r3" / "diff.md").exists()


def test_numbers_flagged_end_to_end(tmp_path):
    its = inject_dataset([it for it in items() if "100" in it["source"] or "25" in it["source"]], rate=1.0, seed=1, kinds=["number"])
    ds = tmp_path / "n.jsonl"
    ds.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in its), encoding="utf-8")
    assert main(["--dataset", str(ds), "--langs", "hi", "--out", str(tmp_path / "o"), "--cache-dir", str(tmp_path / "c"),
                 "--no-judge"], backend=FakeBackend()) == 0
    s = json.loads((tmp_path / "o" / "summary.json").read_text(encoding="utf-8"))
    assert s["calibration_metrics"]["per_category"]["number"]["recall"] == 1.0


def test_text_injection_on_audio_item_keeps_spoken_text():
    its = [{**it, "audio": "a.wav"} for it in items()]
    out = inject_dataset(its, rate=1.0, seed=3, kinds=["omission"], audio_dir=None)
    for a, b in zip(its, out):
        if b["translation"] != a["translation"]:
            assert b["speech_text"] == a["translation"]


def test_term_injection_misspells_a_term_and_labels_it():
    out = inject_dataset(items(), rate=1.0, seed=4, kinds=["term"])
    done = 0
    for a, b in zip(items(), out):
        if b["labels"]:
            lab = b["labels"][0]
            assert lab["type"] == "term" and b["translation"] != a["translation"]
            s, e = lab["span"]
            assert 0 <= s < e <= len(b["translation"])
            # one word was misspelled, not replaced by an unrelated one: edit distance is tiny
            from bodhan_eval.align import levenshtein
            assert levenshtein(a["translation"], b["translation"]) <= 3
            done += 1
    assert done >= 6


def test_rubric_separates_term_from_mistranslation():
    from bodhan_eval.judge import RUBRIC
    assert "ORDINARY word" in RUBRIC and "NAME" in RUBRIC and "prefer `term`" in RUBRIC


def test_holdout_split_is_disjoint_and_reported(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    many = []
    for k in range(40):
        tr, src = HI[k % 3]
        many.append({"id": f"h{k}", "lang": "hi", "source": src, "translation": tr})
    ds = tmp_path / "big.jsonl"
    ds.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in inject_dataset(many, rate=0.5, seed=1)), encoding="utf-8")
    out = tmp_path / "o"
    assert main(["--dataset", str(ds), "--langs", "hi", "--out", str(out), "--cache-dir", str(tmp_path / "c"), "--no-judge",
                 "--concurrency", "2"], backend=FakeBackend()) == 0
    s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    sp = s["split"]
    assert sp["enabled"] and sp["n_holdout"] > 0 and sp["n_tune"] + sp["n_holdout"] == 40
    assert s["threshold_tuning"].get("n_labelled", sp["n_tune"]) == sp["n_tune"]       # tuner never saw held-out items
    assert s["calibration_metrics"]["n_labelled_items"] == sp["n_holdout"]
    rows = [json.loads(l) for l in (out / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(1 for r in rows if r["split"] == "holdout") == sp["n_holdout"]
    assert "Held-out evaluation" in (out / "summary.md").read_text(encoding="utf-8")
    # disabled
    out2 = tmp_path / "o2"
    main(["--dataset", str(ds), "--langs", "hi", "--out", str(out2), "--cache-dir", str(tmp_path / "c"), "--no-judge",
          "--holdout-frac", "0"], backend=FakeBackend())
    assert not json.loads((out2 / "summary.json").read_text(encoding="utf-8"))["split"]["enabled"]


def test_injector_balances_categories():
    from collections import Counter
    many = [{"id": f"b{k}", "lang": "hi", "source": HI[k % 3][1], "translation": HI[k % 3][0]} for k in range(60)]
    out = inject_dataset(many, rate=1.0, seed=2)
    c = Counter(l["type"] for o in out for l in o["labels"])
    assert set(c) == set(TEXT_KINDS)                   # every kind appears
    assert max(c.values()) - min(c.values()) <= 4      # and none dominates


def test_human_label_sheet_roundtrip(tmp_path):
    from bodhan_eval.labels import import_sheet, make_sheet
    ds = tmp_path / "d.jsonl"
    ds.write_text("\n".join(json.dumps({**it, "labels": [{"type": "number"}]}, ensure_ascii=False) for it in items()), encoding="utf-8")
    sheet = tmp_path / "s.csv"
    assert make_sheet(str(ds), str(sheet), 5) == 5
    text = sheet.read_text(encoding="utf-8-sig")
    assert "number" not in text.split("\n", 1)[1]            # injected labels are NOT leaked into the blind sheet
    import csv
    rows = list(csv.reader(open(sheet, encoding="utf-8-sig", newline="")))
    rows[1][4] = "Omission; number"
    rows[2][4] = "bogus"
    with open(sheet, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(rows)
    out = tmp_path / "l.jsonl"
    n, problems = import_sheet(str(sheet), str(out), "alice")
    got = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert n == 5 and len(problems) == 1 and "bogus" in problems[0]
    assert [x["type"] for x in got[0]["labels"]] == ["omission", "number"] and got[0]["labels"][0]["label_source"] == "human:alice"
    assert got[1]["labels"] == [] and got[3]["labels"] == []


def test_label_import_records_who_labelled(tmp_path):
    from bodhan_eval.labels import import_sheet
    import csv
    sheet = tmp_path / "s.csv"
    with open(sheet, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows([["id", "lang", "source", "translation", "types", "notes"], ["a", "hi", "x", "y", "number", ""]])
    out = tmp_path / "l.jsonl"
    import_sheet(str(sheet), str(out), "claude", kind="ai")
    assert json.loads(out.read_text(encoding="utf-8"))["labels"][0]["label_source"] == "ai:claude"


def test_dashboard_is_written_deterministic_and_injection_safe(tmp_path, monkeypatch):
    import re
    monkeypatch.chdir(tmp_path)
    evil = {"id": "x</script><img src=x onerror=alert(1)>", "lang": "hi", "source": "A </script><b>bold</b> & 5 apples.",
            "translation": "5 </script><script>alert(1)</script> सेब", "labels": []}
    ds = tmp_path / "d.jsonl"
    ds.write_text("\n".join(json.dumps(i, ensure_ascii=False) for i in items() + [evil]), encoding="utf-8")
    outs = []
    for name in ("a", "b"):
        assert main(["--dataset", str(ds), "--langs", "hi", "--out", str(tmp_path / name), "--cache-dir", str(tmp_path / "c"),
                     "--no-judge", "--concurrency", "2"], backend=FakeBackend()) == 0
        outs.append((tmp_path / name / "dashboard.html").read_text(encoding="utf-8"))
    assert outs[0] == outs[1]                                   # deterministic
    html = outs[0]
    assert html.count("</script>") == 2                        # only the two real script blocks close
    data = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1)
    assert "<" not in data and ">" not in data                  # every < and > in the data is escaped, so it cannot form a tag
    parsed = json.loads(data)                                   # still valid JSON, round-trips the hostile text
    assert any("</script>" in i["source"] for i in parsed["items"])
