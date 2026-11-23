from bodhan_eval.dashboard import build_from_report, render


def _data(items):
    return {"summary": {"totals": {"items": len(items)}, "by_language": {}, "cost": {},
                        "judge_consistency": {"measured": False}, "calibration_summary": None},
            "items": items, "calibration": {}, "diff": None}


def test_render_self_contained_and_has_items():
    items = [{"id": "a1", "lang": "hi", "status": "flagged", "source": "Water 100",
              "translation": "पानी ९०", "reference": "", "flags": [
                  {"category": "number_unit", "severity": "major", "confidence": 0.9,
                   "source_span": [6, 9], "target_span": None, "evidence": []}],
              "uncertain_categories": [], "judge_verdict": "error",
              "checker_disagreement": False, "signal": None, "cost_inr": 0.0}]
    html = render(_data(items))
    assert "<style>" in html and "<script" in html
    assert "cdn" not in html.lower() and 'src="http' not in html
    assert "a1" in html  # item id embedded in the JSON data block


def test_hostile_string_is_neutralized():
    evil = "</script><img src=x onerror=alert(1)>"
    items = [{"id": "x", "lang": "hi", "status": "clean", "source": evil, "translation": evil,
              "reference": "", "flags": [], "uncertain_categories": [], "judge_verdict": None,
              "checker_disagreement": False, "signal": None, "cost_inr": 0.0}]
    html = render(_data(items))
    # the raw closing tag must not appear verbatim inside the embedded data (</ is escaped)
    assert "</script><img" not in html
    assert "<\\/script>" in html


def test_empty_report_renders():
    html = render(_data([]))
    assert "<html" in html and "Proof" in html


def test_build_from_report_writes_html(tmp_path):
    (tmp_path / "dashboard_data.json").write_text(
        '{"summary":{"totals":{"items":0},"by_language":{},"cost":{},'
        '"judge_consistency":{"measured":false},"calibration_summary":null},'
        '"items":[],"calibration":{},"diff":null}', encoding="utf-8")
    out = build_from_report(str(tmp_path))
    assert out.exists() and out.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
