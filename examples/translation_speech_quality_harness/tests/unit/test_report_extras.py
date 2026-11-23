"""Report extras (P6): MQM-style score, the table of checks that could not run, the CI regression gate."""

from __future__ import annotations

import json
from pathlib import Path

from bodhan_eval import diff, mqm, report
from bodhan_eval.models import Flag, Item, ItemResult


def flag(category: str, severity: str, span: tuple[int, int] | None = (0, 4)) -> Flag:
    return Flag(category, severity, 0.9, span, None, "text", None, [], ["judge"])


def result(source: str, flags: list[Flag], checks: dict | None = None, lang: str = "hi") -> ItemResult:
    return ItemResult(item=Item(id="x", lang=lang, source=source, translation="t"), flags=flags, checks=checks or {})


def test_mqm_penalises_by_severity_per_source_word() -> None:
    twenty = " ".join(["word"] * 20)
    assert mqm.item_score(result(twenty, [])) == 100.0
    assert mqm.item_score(result(twenty, [flag("omission", "major")])) == 75.0  # 100 - 100 * 5 / 20
    assert mqm.item_score(result(twenty, [flag("number", "critical"), flag("term", "minor")])) == 45.0
    assert mqm.item_score(result("Two words.", [flag("omission", "critical")])) == 0.0  # floored at 0
    assert mqm.language_score([result(twenty, []), result(twenty, [flag("omission", "major")])]) == 87.5


def test_checks_that_could_not_run_are_counted_with_their_reasons() -> None:
    rs = [
        result("s", [], {"asr": {"status": "ok"}, "judge": {"status": "ok"}}),
        result("s", [], {"asr": {"status": "error", "reason": "retries_exhausted: rate limited"}, "judge": {"status": "ok"}}),
        result("s", [], {"asr": {"status": "skipped", "reason": "no audio"}, "judge": {"status": "ok"}}),
    ]
    table = report.check_table(rs)
    assert table["asr"] == {"ok": 1, "error": 1, "skipped": 1, "not_applicable": 0,
                            "reasons": {"retries_exhausted: rate limited": 1, "no audio": 1}}  # fmt: skip
    md = "\n".join(report.markdown_checks(table))
    assert "| asr | 1 | 1 | 1 | retries_exhausted: rate limited (1); no audio (1) |" in md
    assert "| judge | 3 | 0 | 0 |" in md


def records(*flags: dict) -> list[dict]:
    return [{"id": "x", "lang": "hi", "flags": list(flags)}]


def fdict(category: str, severity: str, span: list[int]) -> dict:
    return {"category": category, "severity": severity, "span": span, "source_span": None, "text": "t",
            "confidence": 0.9, "evidence": [], "checks": ["judge"]}  # fmt: skip


def test_regression_gate_counts_new_major_problems(tmp_path: Path) -> None:
    (tmp_path / "base").mkdir()
    (tmp_path / "base" / "items.jsonl").write_text(json.dumps(records(fdict("number", "major", [0, 2]))[0]) + "\n")
    out = tmp_path / "cur"
    out.mkdir()
    cur = records(fdict("number", "major", [0, 2]), fdict("omission", "critical", [5, 9]), fdict("term", "minor", [10, 12]))
    counts = diff.write_diff(out, tmp_path / "base", cur)
    assert counts == {"new_problems": 2, "new_major_or_worse": 1, "fixed_problems": 0}
    assert diff.gate_exit_code(counts, max_new_major=0) == 3
    assert diff.gate_exit_code(counts, max_new_major=1) == 0


def test_ci_gate_needs_a_baseline() -> None:
    import pytest

    from bodhan_eval.cli import main

    with pytest.raises(SystemExit) as e:
        main(["--dataset", "d.jsonl", "--out", "o", "--fail-on-regression"])
    assert e.value.code == 2


def test_dashboard_embeds_the_items_safely_and_deterministically() -> None:
    from bodhan_eval import dashboard

    r = result("Water </script><script>alert(1)</script>", [flag("number", "critical", (0, 1))])
    page = dashboard.render([r], "demo.jsonl")
    assert page == dashboard.render([r], "demo.jsonl")
    assert page.count("</script>") == 2  # only the page's own two script tags
    payload = page.split('type="application/json">', 1)[1].split("</script>", 1)[0]
    assert json.loads(payload.replace("<\/", "</"))[0]["flags"][0]["category"] == "number"
