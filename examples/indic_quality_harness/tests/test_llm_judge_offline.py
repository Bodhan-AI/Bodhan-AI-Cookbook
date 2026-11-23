"""Offline tests for the LLM judge: a fake Ollama session, no network."""

import json

import pytest
import requests
from bodhan_eval.llm_judge import (
    PROMPT_HASH,
    JudgeResponseError,
    JudgeUnavailableError,
    OllamaJudge,
    findings_to_checks,
    parse_judge_response,
)

SOURCE = "The train will arrive at 6 PM."
TRANSLATION = "ट्रेन सुबह 8 बजे पहुंचेगी।"


def finding(**overrides):
    base = {
        "evidence_source": "translation",
        "category": "number_unit",
        "severity": "high",
        "span": "सुबह 8 बजे",
        "explanation": "6 PM became 8 AM.",
        "confidence": 0.9,
    }
    base.update(overrides)
    return base


class FakeResponse:
    def __init__(self, payload, status=200, raw=None):
        self.payload = payload
        self.status_code = status
        self.raw = raw

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        if self.raw is not None:
            raise ValueError("not json")
        return self.payload


class FakeSession:
    """Returns queued model contents; records every POST payload."""

    def __init__(self, contents=None, exc=None, models=("llama3.2:3b",)):
        self.contents = list(contents or [])
        self.exc = exc
        self.models = models
        self.posts = []

    def post(self, url, json=None, timeout=None):
        self.posts.append(json)
        if self.exc:
            raise self.exc
        content = self.contents.pop(0)
        return FakeResponse({"message": {"content": content}})

    def get(self, url, timeout=None):
        if self.exc:
            raise self.exc
        return FakeResponse({"models": [{"name": m} for m in self.models]})


def make_judge(tmp_path, session, **kwargs):
    return OllamaJudge(cache_path=tmp_path / "judge.json", session=session, **kwargs)


def content(*findings, **extra):
    return json.dumps({"findings": list(findings), **extra}, ensure_ascii=False)


# -- parsing and validation ----------------------------------------------------

def test_valid_finding_is_kept():
    parsed = parse_judge_response(content(finding()), SOURCE, TRANSLATION)
    assert len(parsed.findings) == 1
    assert parsed.findings[0].span == "सुबह 8 बजे"
    assert parsed.rejected == []


def test_clean_translation_has_no_findings():
    parsed = parse_judge_response('{"findings": []}', SOURCE, TRANSLATION)
    assert parsed.findings == [] and parsed.rejected == []


def test_invalid_category_drops_only_that_finding():
    parsed = parse_judge_response(
        content(finding(category="meaning_error"), finding()), SOURCE, TRANSLATION
    )
    assert len(parsed.findings) == 1
    assert parsed.rejected[0]["reason"] == "schema"


def test_invalid_severity_and_confidence_rejected():
    parsed = parse_judge_response(
        content(finding(severity="critical"), finding(confidence=1.5)), SOURCE, TRANSLATION
    )
    assert parsed.findings == []
    assert [r["reason"] for r in parsed.rejected] == ["schema", "schema"]


def test_span_not_in_translation_rejected_but_valid_kept():
    parsed = parse_judge_response(
        content(finding(span="शाम 6 बजे"), finding()), SOURCE, TRANSLATION
    )
    assert len(parsed.findings) == 1
    assert parsed.rejected[0]["reason"].startswith("span_not_found_in_translation")


def test_non_omission_evidence_from_source_rejected():
    parsed = parse_judge_response(
        content(finding(evidence_source="source", span="6 PM")), SOURCE, TRANSLATION
    )
    assert parsed.findings == []
    assert parsed.rejected[0]["reason"].startswith("wrong_evidence_source")


def test_omission_evidence_must_come_from_source():
    good = finding(category="omission", evidence_source="source", span="6 PM")
    wrong_side = finding(category="omission", evidence_source="translation", span="सुबह")
    not_in_source = finding(category="omission", evidence_source="source", span="tomorrow")

    parsed = parse_judge_response(content(good, wrong_side, not_in_source), SOURCE, TRANSLATION)

    assert [f.span for f in parsed.findings] == ["6 PM"]
    reasons = [r["reason"] for r in parsed.rejected]
    assert reasons[0].startswith("wrong_evidence_source")
    assert reasons[1].startswith("span_not_found_in_source")


def test_span_matching_tolerates_whitespace():
    parsed = parse_judge_response(content(finding(span="सुबह  8\nबजे")), SOURCE, TRANSLATION)
    assert len(parsed.findings) == 1


def test_rewrite_fields_are_never_surfaced():
    parsed = parse_judge_response(
        content(finding(), corrected_translation="ट्रेन शाम 6 बजे पहुंचेगी।"),
        SOURCE, TRANSLATION,
    )
    assert len(parsed.findings) == 1
    assert parsed.rejected == [{"reason": "unexpected_top_level_field", "field": "corrected_translation"}]


def test_extra_field_inside_finding_rejected():
    parsed = parse_judge_response(
        content(finding(correction="शाम 6 बजे")), SOURCE, TRANSLATION
    )
    assert parsed.findings == [] and parsed.rejected[0]["reason"] == "schema"


@pytest.mark.parametrize("raw", ["not json", "[]", '{"result": []}', '{"findings": "none"}'])
def test_malformed_output_raises(raw):
    with pytest.raises(JudgeResponseError):
        parse_judge_response(raw, SOURCE, TRANSLATION)


def test_duplicate_findings_are_collapsed():
    parsed = parse_judge_response(content(finding(), finding()), SOURCE, TRANSLATION)
    assert len(parsed.findings) == 1


# -- Ollama client behaviour ----------------------------------------------------

def test_request_uses_temperature_zero_and_schema(tmp_path):
    session = FakeSession([content(finding())])
    judge = make_judge(tmp_path, session)

    outcome = judge.judge(SOURCE, TRANSLATION, "hi")

    assert outcome.status == "ok"
    payload = session.posts[0]
    assert payload["options"]["temperature"] == 0
    assert payload["stream"] is False
    assert "properties" in payload["format"]
    assert "Hindi" in payload["messages"][1]["content"]


def test_cache_hit_does_not_call_model_again(tmp_path):
    session = FakeSession([content(finding())])
    judge = make_judge(tmp_path, session)

    first = judge.judge(SOURCE, TRANSLATION, "hi")
    second = judge.judge(SOURCE, TRANSLATION, "hi")

    assert len(session.posts) == 1
    assert judge.inference_calls == 1
    assert first.cached is False and second.cached is True
    assert [f.span for f in second.findings] == ["सुबह 8 बजे"]


def test_cache_persists_across_instances(tmp_path):
    make_judge(tmp_path, FakeSession([content(finding())])).judge(SOURCE, TRANSLATION, "hi")

    session = FakeSession([])
    outcome = make_judge(tmp_path, session).judge(SOURCE, TRANSLATION, "hi")

    assert outcome.cached is True and session.posts == []


def test_cache_miss_for_any_changed_input(tmp_path):
    session = FakeSession([content()] * 4)
    judge = make_judge(tmp_path, session)

    judge.judge(SOURCE, TRANSLATION, "hi")
    judge.judge(SOURCE, TRANSLATION + " ", "hi")       # translation changed
    judge.judge(SOURCE + " ", TRANSLATION, "hi")       # source changed
    judge.judge(SOURCE, TRANSLATION, "bn")             # language changed

    assert len(session.posts) == 4


def test_cache_key_includes_model_and_prompt(monkeypatch):
    a = OllamaJudge(model="llama3.2:3b")
    b = OllamaJudge(model="qwen2.5:7b")
    assert a.cache_key(SOURCE, TRANSLATION, "hi") != b.cache_key(SOURCE, TRANSLATION, "hi")

    import bodhan_eval.llm_judge as module
    before = a.cache_key(SOURCE, TRANSLATION, "hi")
    monkeypatch.setattr(module, "PROMPT_HASH", "a-different-prompt")
    assert a.cache_key(SOURCE, TRANSLATION, "hi") != before
    assert len(PROMPT_HASH) == 16


def test_ollama_unavailable_is_reported_not_raised(tmp_path):
    session = FakeSession(exc=requests.ConnectionError("connection refused"))
    judge = make_judge(tmp_path, session)

    outcome = judge.judge(SOURCE, TRANSLATION, "hi")

    assert outcome.status == "unavailable"
    assert "connection refused" in outcome.error
    assert judge.cache.data == {}


def test_timeout_is_reported_as_unavailable(tmp_path):
    judge = make_judge(tmp_path, FakeSession(exc=requests.Timeout("timed out")))
    assert judge.judge(SOURCE, TRANSLATION, "hi").status == "unavailable"


def test_check_available_raises_when_down_or_model_missing(tmp_path):
    with pytest.raises(JudgeUnavailableError):
        make_judge(tmp_path, FakeSession(exc=requests.ConnectionError("down"))).check_available()
    with pytest.raises(JudgeUnavailableError, match="ollama pull"):
        make_judge(tmp_path, FakeSession(models=("other:1b",))).check_available()
    make_judge(tmp_path, FakeSession()).check_available()


def test_malformed_output_is_an_error_and_not_cached(tmp_path):
    session = FakeSession(["Sure! Here are the errors: ...", content(finding())])
    judge = make_judge(tmp_path, session)

    first = judge.judge(SOURCE, TRANSLATION, "hi")
    second = judge.judge(SOURCE, TRANSLATION, "hi")

    assert first.status == "error"
    assert second.status == "ok" and second.cached is False
    assert len(session.posts) == 2


def test_evaluate_keeps_original_api(tmp_path):
    judge = make_judge(tmp_path, FakeSession([content(finding(), finding(span="नहीं है"))]))
    result = judge.evaluate(SOURCE, TRANSLATION, "Hindi")
    assert [f.span for f in result.findings] == ["सुबह 8 बजे"]

    down = make_judge(tmp_path / "x", FakeSession(exc=requests.ConnectionError("down")))
    with pytest.raises(RuntimeError):
        down.evaluate(SOURCE, TRANSLATION, "hi")

    bad = make_judge(tmp_path / "y", FakeSession(["garbage"]))
    with pytest.raises(ValueError):
        bad.evaluate(SOURCE, TRANSLATION, "hi")

    with pytest.raises(ValueError):
        judge.evaluate("  ", TRANSLATION, "hi")


def test_findings_become_review_checks(tmp_path):
    outcome = make_judge(tmp_path, FakeSession([content(finding())])).judge(SOURCE, TRANSLATION, "hi")
    checks = findings_to_checks(outcome)
    assert checks[0]["category"] == "llm_number_unit"
    assert checks[0]["status"] == "review" and checks[0]["passed"] is None
    assert checks[0]["evidence"]["span"] == "सुबह 8 बजे"
