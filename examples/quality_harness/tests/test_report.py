import json

from bodhan_eval.report import diff, write_reports

SOURCE = "The fort was built in 1650. It has four gates."
OLD = "किला 1650 में बना था। इसमें चार द्वार हैं।"
NEW = "किला 1655 में बना था। इसमें चार द्वार हैं।"


def flag(category, span, text, severity="major", side="translation"):
    return {"category": category, "alt_categories": [], "severity": severity, "side": side, "span": span, "text": text,
            "confidence": 0.9, "decision": "flag", "sources": ["numbers"], "candidates": [0]}


def rec(id_, translation, flags, lang="hi"):
    cand = {"check": "numbers", "category": "number", "side": "translation", "span": [5, 9], "score": 1.0,
            "standalone": True, "severity": "major", "text": "", "evidence": {"kind": "changed", "source": "1650", "translation": "1655"}}
    return {"id": id_, "lang": lang, "source": SOURCE, "translation": translation, "checks": {"judge": "ok"},
            "candidates": [cand], "flags": flags, "verdict": "flagged" if flags else "clean",
            "mqm": sum({"minor": 1, "major": 5, "critical": 10}[f["severity"]] for f in flags)}


def test_diff_sorts_flags_into_new_resolved_persisting_and_severity_changed():
    gates = flag("term", [22, 40], "इसमें चार द्वार हैं।", "minor")
    base = [rec("a", OLD, [gates]), rec("b", OLD, [flag("omission", [0, 4], "किला")]), rec("gone", OLD, [])]
    run = [rec("a", NEW, [flag("number", [5, 9], "1655"), {**gates, "severity": "major"}]), rec("b", OLD, []), rec("new", OLD, [])]
    d = diff(run, base)
    assert [(x["id"], x["category"]) for x in d["new"]] == [("a", "number")]
    assert d["new"][0]["translation_changed"] == "−“1650” +“1655”"
    assert [(x["id"], x["was"], x["severity"]) for x in d["severity_changed"]] == [("a", "minor", "major")]  # same text: matched
    assert [(x["id"], x["category"]) for x in d["resolved"]] == [("b", "omission")] and d["persisting"] == []
    assert d["only_in_baseline"] == [["gone", "hi"]] and d["only_in_run"] == [["new", "hi"]]
    assert d["delta"]["hi|MQM"] == {"before": 6, "after": 10}


def test_reports_are_written_and_say_what_was_not_measured(tmp_path):
    for name, rows in (("old", [rec("a", OLD, [])]), ("new", [rec("a", NEW, [flag("number", [5, 9], "1655")])])):
        (tmp_path / name).mkdir()
        (tmp_path / name / "items.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    s = write_reports(tmp_path / "new", tmp_path / "old")
    md = (tmp_path / "new" / "summary.md").read_text(encoding="utf-8")
    assert "No `--calibration` labels in this run" in md and "not measured in this run" in md
    assert "| a | hi | 5 | number | major | 1655 | numbers: source says 1650, translation says 1655 |" in md
    assert s["languages"]["hi"]["mqm_per_100_words"] == round(100 * 5 / len(SOURCE.split()), 2) and s["diff"]["new"] == 1
    assert "## New flags (regressions) (1)" in (tmp_path / "new" / "diff.md").read_text(encoding="utf-8")
