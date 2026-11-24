"""Report files: quotes, weighted score, review queue round trip, baseline diff and the CI exit code."""

import csv
import io
import json
from pathlib import Path

from conftest import DATA, make_args, make_clients

from bodhan_eval import calibrate, pipeline, report

ROWS = [json.loads(line) for line in (DATA / "ten.jsonl").read_text(encoding="utf-8").splitlines()]


def dataset(tmp_path, name, seeded: bool) -> Path:
    rows = [dict(r) for r in ROWS]
    if seeded:
        rows[0]["translation"] = rows[0]["translation"].replace("100", "1000")
    p = tmp_path / name
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return p


def run(tmp_path, fake_bodhan, fake_llm, out, **kw):
    bodhan, llm = make_clients(tmp_path, fake_bodhan, fake_llm)
    args = make_args(tmp_path, out=str(tmp_path / out), **kw)
    return pipeline.run(args, bodhan=bodhan, llm=llm), Path(args.out)


def test_quote_marks_gaps_and_trims():
    assert report.quote("abcdef", [1, 3]) == "bc"
    assert report.quote("abc def", [3, 3]) == "abc⟦⟧ def"
    assert report.quote("x" * 300, [0, 300]).endswith("…") and len(report.quote("x" * 300, [0, 300])) == 120
    assert report.quote("", [0, 1]) == "" and report.quote("abc", None) == ""


def test_summary_has_quotes_weighted_score_and_top_failure(tmp_path, fake_bodhan, fake_llm):
    code, out = run(tmp_path, fake_bodhan, fake_llm, "r", dataset=dataset(tmp_path, "s.jsonl", True))
    assert code == 0
    s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    hi = s["per_language"]["hi"]
    assert hi["flags"] == 1 and hi["weighted_errors_per_1000_words"] > 0 and s["per_language"]["ta"]["weighted_errors_per_1000_words"] == 0
    assert s["per_category"] == {"number": {"hi": 1}}
    md = (out / "summary.md").read_text(encoding="utf-8")
    assert "Worst language: **hi**" in md and "t01 / hi" in md and "“100”" in md and "“1000”" in md
    item = next(json.loads(x) for x in (out / "items.jsonl").read_text(encoding="utf-8").splitlines() if x.startswith('{"checks_run"') and '"t01"' in x)
    assert item["flags"][0]["quote"] == {"source": "100", "translation": "1000"}


def test_review_csv_round_trip():
    results = [{"id": "a", "lang": "hi", "review": [{"category": "omission", "severity": "major", "confidence": 0.5, "source_span": [0, 4],
                                                     "translation_span": [0, 3], "checks": ["back_translation"], "quote": {}}], "flags": []},
               {"id": "b", "lang": "ta", "review": [{"category": "number", "severity": "minor", "confidence": 0.4, "source_span": None,
                                                     "translation_span": None, "checks": ["numbers"], "quote": {}}], "flags": []},
               {"id": "c", "lang": "bn", "review": [{"category": "term", "severity": "minor", "confidence": 0.4, "source_span": None,
                                                     "translation_span": None, "checks": ["judge"], "quote": {}}], "flags": []}]
    rows = list(csv.DictReader(io.StringIO(report._review_csv(results))))
    assert [r["id"] for r in rows] == ["a", "b", "c"] and all(r["human_label"] == "" for r in rows)
    rows[0]["human_label"], rows[1]["human_label"] = "omission", "clean"  # c left unanswered
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=report.REVIEW_COLUMNS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    labels, unanswered = report.read_review_csv(buf.getvalue())
    assert unanswered == 1 and set(labels) == {("a", "hi"), ("b", "ta")}
    assert labels[("b", "ta")] == [] and labels[("a", "hi")][0]["span"] == [0, 4] and labels[("a", "hi")][0]["origin"] == "human"


def test_filled_review_csv_works_as_calibration(tmp_path):
    p = tmp_path / "queue.csv"
    p.write_text(",".join(report.REVIEW_COLUMNS) + "\nt01,Hindi,number,major,0.5,\"[14, 17]\",null,,,numbers,number,\n", encoding="utf-8")
    labels, warnings = calibrate.read_label_file(p)
    assert labels[("t01", "hi")][0]["type"] == "number" and warnings == []


def test_diff_finds_regression_and_improvement_and_ci_exit(tmp_path, fake_bodhan, fake_llm):
    clean, seeded = dataset(tmp_path, "clean.jsonl", False), dataset(tmp_path, "seeded.jsonl", True)
    _, base = run(tmp_path, fake_bodhan, fake_llm, "base", dataset=clean)
    code, worse = run(tmp_path, fake_bodhan, fake_llm, "worse", dataset=seeded, baseline=str(base), fail_on_regression=0)
    assert code == 1  # one item gained a major number flag
    d = json.loads((worse / "summary.json").read_text(encoding="utf-8"))["diff"]
    assert d["regressed_items"] == 1 and d["improved_items"] == 0 and d["new_flags_per_category"] == {"number": 1}
    md = (worse / "diff.md").read_text(encoding="utf-8")
    assert "## Regressions" in md and "t01 / hi" in md and "“1000”" in md

    code, better = run(tmp_path, fake_bodhan, fake_llm, "better", dataset=clean, baseline=str(worse), fail_on_regression=0)
    assert code == 0
    md = (better / "diff.md").read_text(encoding="utf-8")
    assert "## Improvements" in md and "“1000”" in md and "## Regressions" not in md  # quoted from the baseline's items.jsonl


def test_unreadable_baseline_warns(tmp_path, fake_bodhan, fake_llm):
    code, out = run(tmp_path, fake_bodhan, fake_llm, "r", baseline=str(tmp_path / "missing"), fail_on_regression=0)
    assert code == 0 and "cannot read the baseline" in (out / "diff.md").read_text(encoding="utf-8")


def test_flags_on_clean_labelled_items_are_listed(tmp_path, fake_bodhan, fake_llm):
    rows = [dict(r) for r in ROWS]
    rows[0]["translation"] = rows[0]["translation"].replace("100", "1000")
    rows[0]["labels"] = []  # labelled clean, but the output has an error
    p = tmp_path / "d.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    _, out = run(tmp_path, fake_bodhan, fake_llm, "r", dataset=p)
    md = (out / "summary.md").read_text(encoding="utf-8")
    assert "## Flags on items labelled clean" in md and "t01 / hi" in md.split("## Flags on items labelled clean")[1]


def test_narrative_from_llm_or_fixed_fallback(tmp_path, fake_bodhan, fake_llm):
    fake_llm.reply = lambda body: ("Hindi is the weakest language and number errors are the most frequent; most targets were met."
                                   if "quality check" in json.dumps(body) else "{}")
    _, out = run(tmp_path, fake_bodhan, fake_llm, "r", dataset=dataset(tmp_path, "s.jsonl", True))
    s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert s["narrative"]["by"] == "llm" and "Written by the LLM" in (out / "summary.md").read_text(encoding="utf-8")
    fake_llm.reply = "{}"  # not a paragraph: the fixed sentence is used
    _, out = run(tmp_path, fake_bodhan, fake_llm, "r2", dataset=dataset(tmp_path, "c.jsonl", False))
    s = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert s["narrative"] == {"by": "fixed", "text": "0 of 10 items have at least one flagged error."}


def test_baseline_may_be_the_output_folder(tmp_path, fake_bodhan, fake_llm):
    _, out = run(tmp_path, fake_bodhan, fake_llm, "same", dataset=dataset(tmp_path, "c.jsonl", False))
    _, out = run(tmp_path, fake_bodhan, fake_llm, "same", dataset=dataset(tmp_path, "s.jsonl", True), baseline=str(out))
    d = json.loads((out / "summary.json").read_text(encoding="utf-8"))["diff"]
    assert d["regressed_items"] == 1 and d["compared_items"] == 10


def test_dashboard_embeds_findings_safely(tmp_path):
    results = [{"id": "a", "lang": "hi", "flags": [{"category": "number", "severity": "major", "confidence": 0.9, "checks": ["numbers"],
                                                   "quote": {"source": "</script><b>x", "translation": "1000"}}], "review": []}]
    html = report._dashboard_html(results)
    data = html.split('type="application/json">')[1].split("</script>")[0]
    assert json.loads(data.replace("<\/", "</"))[0]["source"] == "</script><b>x"
