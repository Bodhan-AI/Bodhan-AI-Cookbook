"""summary.md, diff.md, quality score and the regression exit code."""

import json

from test_pipeline import NUM_TR, FakeLLM, _run

from bodhan_eval import report


def row(iid, flags, lang="hi", source="one two three four five six seven eight nine ten",
        translation="क ख ग घ ङ च छ ज झ ञ"):  # fmt: skip
    return {"id": iid, "lang": lang, "source": source, "translation": translation,
            "spoken_text": translation, "verdict": "major" if flags else "clean",
            "flags": flags}  # fmt: skip


def flag(cat, span, sev="major", conf=0.8):
    return {"category": cat, "severity": sev, "confidence": conf, "span": span,
            "evidence": f"{cat} here", "details": {"checks": ["numbers"]}}  # fmt: skip


def test_quality_score():
    rows = [row("a", []), row("b", [flag("number", [0, 1], "minor")])]
    q = report.quality(rows)
    assert q["overall"] == 95.0  # 1 penalty point over 20 words
    assert report.quality([row("c", [flag("omission", [0, 1], "critical")] * 3)])["overall"] == 0.0
    assert report.quality([])["overall"] is None


def test_diff_classifies_new_fixed_changed():
    base = [row("a", [flag("number", [0, 3])]), row("b", [flag("term", [5, 9])]),
            row("c", [flag("omission", [2, 4])]), row("old", [])]  # fmt: skip
    cur = [row("a", [flag("number", [1, 3])]),  # same
           row("b", []),  # fixed
           row("c", [flag("mistranslation", [2, 4]), flag("addition", [8, 9])]),  # changed+new
           row("brand-new", [])]  # fmt: skip
    d = report.compute_diff(cur, base)
    assert d["counts"] == {"new": 1, "fixed": 1, "changed": 1, "unchanged": 1}
    assert d["only_in_current"] == ["brand-new"] and d["only_in_baseline"] == ["old"]
    md = report.render_diff(d, "base/")
    assert "1 new" in md and "Fixed (no longer flagged)" in md and "omission/major →" in md


def test_pipeline_writes_summary_md(tmp_path, monkeypatch):
    items, summary, out = _run(tmp_path, monkeypatch, ["--judge-repeats", "2"])
    md = (out / "summary.md").read_text()
    for section in ("## Headline", "## By language", "## Top failures",
                    "## Skipped steps and input problems"):  # fmt: skip
        assert section in md
    assert "## Cost" not in md  # fakes record no spend, so the empty table is left out
    assert "`num` (hi)" in md and "**1898**" in md  # flagged text is highlighted
    assert summary["quality"]["overall"] < 100
    # same inputs → same report (cost table aside, the flags section is deterministic)
    _, _, out2 = _run(tmp_path / "again", monkeypatch, ["--judge-repeats", "2"])
    top = lambda p: p.read_text().split("## Top failures")[1].split("## Skipped")[0]  # noqa: E731
    assert top(out / "summary.md") == top(out2 / "summary.md")


def test_regression_exit_code(tmp_path, monkeypatch):
    _, _, base = _run(tmp_path / "base", monkeypatch)

    class WorseLLM(FakeLLM):
        async def chat_json(self, messages, **kw):
            user = messages[1]["content"]
            if "[0]समुद्र" in user:  # the clean item now gets a critical error
                err = {"category": "mistranslation", "severity": "critical", "token_start": 0,
                       "token_end": 2, "source_quote": "Water", "explanation": "wrong"}  # fmt: skip
                return {"errors": [err], "verdict": "major"}, False
            return await super().chat_json(messages, **kw)

    monkeypatch.setattr("bodhan_eval.pipeline.make_llm_client", WorseLLM)
    from bodhan_eval import pipeline
    from bodhan_eval.cli import build_parser

    ds = tmp_path / "base" / "demo.jsonl"
    args = build_parser().parse_args([
        "--dataset", str(ds), "--langs", "hi", "--out", str(tmp_path / "cur"),
        "--cache-dir", str(tmp_path / "cache2"), "--log-level", "ERROR",
        "--baseline", str(base), "--fail-on-regression", "0.5"])  # fmt: skip
    monkeypatch.setattr(pipeline, "BodhanClient", __import__("test_pipeline").FakeBodhan)
    assert pipeline.run(args) == 3
    s = json.loads((tmp_path / "cur" / "summary.json").read_text())
    assert s["diff"]["counts"]["new"] >= 1 and s["diff"]["quality"]["delta"] < -0.5
    assert "diff.md" in {p.name for p in (tmp_path / "cur").iterdir()}
    assert NUM_TR  # imported fixture text still matches the shared dataset


def test_targets_section_marks_misses_with_reasons():
    from bodhan_eval.report import _targets_section

    per = {
        "number": {"tp": 9, "fp": 0, "fn": 1, "labels": 10, "recall": 0.9},
        "term": {"tp": 1, "fp": 0, "fn": 4, "labels": 5, "recall": 0.2},
    }
    cal = {
        "metrics": {"per_category": per, "clean_flag_rate": 0.25, "span_overlap_rate": 0.9},
        "category_accuracy": 0.8,
    }
    md = "\n".join(_targets_section(cal, {"ran": True, "verdict_agreement": 0.95}))
    rows = {line.split("|")[1].strip(): line for line in md.splitlines() if line.startswith("| ")}
    assert "**missed**" in rows["Detection recall"] and "term 20%" in rows["Detection recall"]
    assert "**missed**" in rows["False alarms"]
    assert "| met |" in rows["Localization"] and "| met |" in rows["Judge consistency"]
