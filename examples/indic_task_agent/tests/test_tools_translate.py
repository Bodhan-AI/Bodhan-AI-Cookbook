"""translate_text over the fake backend (echo translator unless scripted)."""
from __future__ import annotations

from pathlib import Path

import pytest

from bodhan_mcp.core import catalog
from bodhan_mcp.core.errors import BodhanError
from bodhan_mcp.tools.translate import translate_text


def write(tmp_path, text, name="src.md"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


def numbered(n: int, per: int = 100, start: int = 0) -> list[str]:
    return [f"Paragraph {i:04d} " + "word " * ((per - 15) // 5) + "end" for i in range(start, start + n)]


def test_an_11k_document_with_a_table_is_one_request(bodhan, tmp_path):
    table = "| item | qty |\n|---|---|\n" + "\n".join(f"| thing {i} | {i} |" for i in range(20))
    doc = "# Title\n\n" + "\n\n".join(numbered(95, 110)) + "\n\n" + table
    assert 10_500 < len(doc) < 12_000
    res = translate_text(write(tmp_path, doc), "ta", out_dir=str(tmp_path / "o"))
    assert len(bodhan.requests) == 1 and res.segments == 1 and res.requests == 1
    assert bodhan.of("mt")[0].json["messages"][0]["content"] == doc
    assert "| thing 19 | 19 |" in Path(res.text_path).read_text(encoding="utf-8")


def test_a_13k_document_splits_at_paragraphs_and_rejoins_in_order(bodhan, tmp_path):
    paras = ["# Part one"] + numbered(60, 110) + ["## Part two"] + numbered(60, 110, 60)
    doc = "\n\n".join(paras)
    assert 12_000 < len(doc) < 15_000
    res = translate_text(write(tmp_path, doc), "hi", out_dir=str(tmp_path / "o"))
    sent = [r.json["messages"][0]["content"] for r in bodhan.of("mt")]
    assert len(sent) >= 2 and res.segments == len(sent)
    assert all(len(s) <= 12_000 for s in sent)
    for s in sent:  # every piece begins and ends on a paragraph boundary
        assert s.split("\n\n")[0] in paras and s.split("\n\n")[-1] in paras
    out = Path(res.text_path).read_text(encoding="utf-8")
    assert out.split("\n\n") == paras
    assert any(w.startswith("split_into_") for w in res.warnings)


def test_request_shape(bodhan, tmp_path):
    translate_text(write(tmp_path, "Hello there."), "ta", script="roman", out_dir=str(tmp_path / "o"))
    (req,) = bodhan.requests
    assert req.path == "/v1/chat/completions" and req.model == "indic-translate"
    assert req.json["messages"] == [{"role": "user", "content": "Hello there."}]
    assert req.json["target_language_code"] == "ta" and req.json["target_script"] == "roman"
    assert req.key_var == "BODHAN_TRANSLATE_KEY"


def test_truncation_halves_the_segment_and_retries_once(bodhan, tmp_path):
    src = "First half sentence here. " * 4 + "\n\n" + "Second half sentence here. " * 4
    bodhan.script("mt", {"text": "cut", "finish": "length", "tokens": 5})
    res = translate_text(write(tmp_path, src), "ta", out_dir=str(tmp_path / "o"))
    sent = [r.json["messages"][0]["content"] for r in bodhan.of("mt")]
    assert len(sent) == 3 and sent[1] + sent[2] == sent[0]
    assert res.requests == 2 and not res.truncated
    assert any("resplit" in w for w in res.warnings)


def test_second_truncation_raises(bodhan, tmp_path):
    bodhan.script("mt", *[{"text": "cut", "finish": "length"} for _ in range(2)])
    with pytest.raises(BodhanError) as exc:
        translate_text(write(tmp_path, "Some words to translate. " * 20), "ta", out_dir=str(tmp_path / "o"))
    assert exc.value.code == "TRANSLATE_TRUNCATED"
    assert len(bodhan.requests) == 2


def test_cost_is_settled_from_reported_tokens(bodhan, tmp_path):
    bodhan.script("mt", {"text": "x", "tokens": 5000})
    res = translate_text(write(tmp_path, "Hello there."), "ta", out_dir=str(tmp_path / "o"))
    assert res.output_tokens == 5000 and res.cost_inr == pytest.approx(0.10)


def test_rerun_is_served_from_cache(bodhan, tmp_path):
    p = write(tmp_path, "Hello there.")
    translate_text(p, "ta", out_dir=str(tmp_path / "o"))
    res = translate_text(p, "ta", out_dir=str(tmp_path / "o"))
    assert len(bodhan.requests) == 1 and res.requests == 0


def test_cached_rerun_costs_nothing(bodhan, tmp_path):
    p = write(tmp_path, "Hello there.")
    translate_text(p, "ta", out_dir=str(tmp_path / "o"))
    assert translate_text(p, "ta", out_dir=str(tmp_path / "o")).cost_inr == 0


def test_french_is_refused_with_the_supported_set_and_no_request(bodhan, tmp_path):
    with pytest.raises(BodhanError) as exc:
        translate_text(write(tmp_path, "Hello"), "fr")
    assert exc.value.code == "UNSUPPORTED_LANGUAGE"
    assert all(code in exc.value.hint for code in catalog.MT_LANGUAGES)
    assert bodhan.requests == []


def test_bhojpuri_is_asr_only(bodhan, tmp_path):
    with pytest.raises(BodhanError) as exc:
        translate_text(write(tmp_path, "Hello"), "bho")
    assert exc.value.code == "UNSUPPORTED_LANGUAGE" and bodhan.requests == []


@pytest.mark.parametrize("target,script", [("en", "roman"), ("ks-Deva", "codemix"), ("ta", "latin")])
def test_script_variants_are_validated_before_any_request(bodhan, tmp_path, target, script):
    with pytest.raises(BodhanError) as exc:
        translate_text(write(tmp_path, "Hello"), target, script=script)
    assert exc.value.code == "UNSUPPORTED_SCRIPT" and bodhan.requests == []


def test_documented_translate_route_is_not_used(bodhan, tmp_path):
    translate_text(write(tmp_path, "Hello"), "ta", out_dir=str(tmp_path / "o"))
    assert all(r.path != "/translate" for r in bodhan.requests)
