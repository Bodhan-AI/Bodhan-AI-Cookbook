import json

import httpx
import pytest
from PIL import Image

from backend import runner
from backend.inference import Inference, ProviderError
from backend.models import SourceBlock, SourceChapter, SourceSection
from backend.report import AttemptLog
from backend.translation.provider import (
    GGUF_MODEL, RATE_PER_OUTPUT_TOKEN, GGUFTranslate, IndicTranslateProvider, ModelTranslation,
    TranslationResponse, gguf_endpoint,
)
from backend.translation.segments import TOKEN, plan_units
from backend.translation.translate import translate_plan
from bodhan_lesson import main


def make_plan(source, text, glossary=None):
    block = SourceBlock("p001-b001", "paragraph", ("p001-b001",), text=text)
    section = SourceSection("s001", None, None, None, (block,))
    return plan_units(SourceChapter(source, (section,)), glossary)


def test_english_to_hindi_uses_one_leg_and_restores_literals():
    calls = []
    plan = make_plan("en", "Measure 12 cm.")

    def translate(unit_id, source, target, text):
        calls.append((unit_id, source, target, text))
        return TranslationResponse(text.replace("Measure", "मापें"), ("a000001",))

    result = translate_plan(plan, ("hi",), translate)

    [unit] = result.targets[0].units
    assert calls == [(unit.unit_id, "en", "hi", plan.units[0].payload)]
    assert unit.route == "direct"
    assert unit.text == "मापें 12 cm."
    assert unit.legs[0].attempt_ids == ("a000001",)
    assert unit.legs[0].placeholder_check.status == "passed"
    assert result.pivots == ()


def test_hindi_to_tamil_and_bengali_share_the_checked_english_pivot():
    calls = []
    plan = make_plan("hi", "लंबाई 12 cm है।")

    def translate(unit_id, source, target, text):
        calls.append((source, target, text))
        token = TOKEN.search(text)[0]
        return TranslationResponse(f"{target} {token}", (f"a{len(calls):06d}",))

    run = translate_plan(plan, ("ta", "bn", "en"), translate)

    assert [(source, target) for source, target, _ in calls] == [
        ("hi", "en"), ("en", "ta"), ("en", "bn"),
    ]
    [pivot] = run.pivots
    assert calls[1][2] == calls[2][2] == pivot.legs[0].protected_payload
    assert calls[1][2] != pivot.text
    for target in run.targets[:2]:
        [unit] = target.units
        assert unit.route == "pivot"
        assert unit.pivot_unit_id == pivot.unit_id
        assert unit.legs[0].attempt_ids == ("a000001",)
        assert unit.text == f"{target.target_lang} 12 cm"
    assert run.targets[2].units[0].text == pivot.text


def test_same_language_reuses_exact_source():
    text = "यह 12 cm है।"
    plan = make_plan("hi", text)

    def no_call(*args):
        raise AssertionError("The provider must not be called.")

    run = translate_plan(plan, ("hi",), no_call)
    [unit] = run.targets[0].units
    assert unit.route == "reuse"
    assert unit.text == text
    assert not unit.legs and not run.pivots


def test_missing_pivot_token_blocks_both_targets_without_retrying_the_pivot():
    calls = []

    def translate(unit_id, source, target, text):
        calls.append((source, target))
        return TranslationResponse("The length is unknown.", ("a000001",))

    run = translate_plan(make_plan("hi", "लंबाई 12 cm है।"), ("ta", "bn"), translate)

    assert calls == [("hi", "en")]
    [pivot] = run.pivots
    [leg] = pivot.legs
    assert leg.placeholder_check.status == "failed"
    assert leg.placeholder_check.observed == {}
    assert leg.protected_payload is None
    assert run.failed_languages == ("ta", "bn")
    assert all(target.units[0].reason.startswith("English pivot failed") for target in run.targets)


def test_glossary_applies_known_pivot_term_and_reports_unverified_miss():
    plan = make_plan("hi", "कोण और रेखा", {
        "कोण": {"en": "angle", "ta": "கோணம்", "bn": "কোণ"},
        "रेखा": {"ta": "கோடு", "bn": "রেখা"},
    })

    def translate(unit_id, source, target, text):
        if target == "en":
            assert text == "कोण और रेखा"
            return TranslationResponse("angle and line")
        [token] = TOKEN.findall(text)
        assert text == f"{token} and line"
        return TranslationResponse(f"{token} ...")

    run = translate_plan(plan, ("ta", "bn"), translate)

    for target, preferred in zip(run.targets, ("கோணம்", "কোণ"), strict=True):
        [unit] = target.units
        applied, missed = unit.terminology
        assert unit.text == f"{preferred} ..."
        assert (applied.status, applied.applied_count, applied.observed_count) == ("seen", 1, 1)
        assert (missed.status, missed.applied_count, missed.observed_count) == ("missing", 0, 0)
        assert target.status == "completed_with_misses"


def test_pivot_cuda_oom_uses_hosted_once_and_accounts_for_output_tokens():
    log = AttemptLog()
    released = []
    requests = []

    def local(text, target):
        if target == "en":
            raise RuntimeError("CUDA out of memory.")
        return ModelTranslation(f"{text} {target}")

    def hosted(request):
        assert str(request.url) == "https://api.bodhan.ai/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        requests.append(body)
        assert body["target_language_code"] == "en"
        token = TOKEN.search(body["messages"][0]["content"])[0]
        return httpx.Response(200, json={
            "choices": [{"message": {"content": f"Length: {token}"}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 17},
        })

    with httpx.Client(transport=httpx.MockTransport(hosted)) as client:
        provider = IndicTranslateProvider(
            "local-first", Inference(log), client, {"BODHAN_TRANSLATE_KEY": "test-key"},
            local, lambda: released.append(True),
        )
        plan = make_plan("hi", "लंबाई 12 cm है।")
        run = translate_plan(plan, ("ta", "bn"), provider.translate)

    assert released == [True]
    assert requests == [{
        "model": "indic-translate",
        "messages": [{"role": "user", "content": plan.units[0].payload}],
        "target_language_code": "en",
    }]
    assert [attempt.backend for attempt in log.attempts] == ["local", "hosted", "local", "local"]
    assert log.attempts[0].error_category == "cuda_oom"
    assert log.attempts[1].fallback_reason == "Local CUDA out of memory"
    assert log.attempts[1].usage.quantity == 17
    assert log.attempts[1].usage.cost_inr == 17 * RATE_PER_OUTPUT_TOKEN
    [pivot] = run.pivots
    assert pivot.protected_spans == plan.units[0].protected_spans
    assert pivot.legs[0].attempt_ids == ("a000001", "a000002")
    assert run.targets[0].units[0].text == "Length: 12 cm ta"


def test_hosted_truncation_is_visible_and_still_records_billed_usage():
    log = AttemptLog()

    def hosted(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "आधा"}, "finish_reason": "length"}],
            "usage": {"completion_tokens": 10},
        })

    def no_local(*args):
        raise AssertionError("Hosted mode must not initialize a local model.")

    with httpx.Client(transport=httpx.MockTransport(hosted)) as client:
        provider = IndicTranslateProvider(
            "hosted", Inference(log), client, {"BODHAN_TRANSLATE_KEY": "test-key"}, no_local, no_local,
        )
        run = translate_plan(make_plan("en", "An angle."), ("hi",), provider.translate)
    [unit] = run.targets[0].units
    assert unit.text is None and "truncated" in unit.reason
    assert log.attempts[0].usage.quantity == 10


def test_translate_checkpoint_keeps_independent_targets_and_returns_nonzero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BODHAN_BACKEND", "local-first")
    scans = tmp_path / "scans"
    scans.mkdir()
    with Image.new("RGB", (200, 300), "white") as image:
        image.save(scans / "page-1.png")

    def recognize(self, png, options):
        return {"markdown": "Measure the angle."}

    def translate(self, text, target):
        if target == "hi":
            raise ProviderError("local_setup", "Synthetic setup failure.")
        assert target == "gu"
        return ModelTranslation("કોણ માપો.")

    monkeypatch.setattr(runner.LocalOCR, "__call__", recognize)
    monkeypatch.setattr(runner.LocalTranslate, "__call__", translate)
    out = tmp_path / "out"

    assert main(["--input", str(scans), "--out", str(out), "--langs", "hi,gu",
                 "--source-lang", "en", "--until", "translate"]) == 1
    failed = json.loads((out / "hi/segments.json").read_text(encoding="utf-8"))
    completed = json.loads((out / "gu/segments.json").read_text(encoding="utf-8"))
    assert failed["status"] == "failed"
    assert failed["units"][0]["reason"] == "Synthetic setup failure."
    assert [attempt["backend"] for attempt in failed["attempts"]] == ["local"]
    assert completed["status"] == "completed"
    assert completed["units"][0]["text"] == "કોણ માપો."
    assert not (out / "work/pivot.en.json").exists()


def test_pivot_checkpoint_writes_shared_attempt_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BODHAN_BACKEND", "local-first")
    scans = tmp_path / "scans"
    scans.mkdir()
    with Image.new("RGB", (200, 300), "white") as image:
        image.save(scans / "page-1.png")

    def recognize(self, png, options):
        return {"markdown": "लंबाई 12 cm है।"}

    def translate(self, text, target):
        token = TOKEN.search(text)[0]
        return ModelTranslation(f"{target} {token}")

    monkeypatch.setattr(runner.LocalOCR, "__call__", recognize)
    monkeypatch.setattr(runner.LocalTranslate, "__call__", translate)
    out = tmp_path / "out"

    assert main(["--input", str(scans), "--out", str(out), "--langs", "ta,bn",
                 "--source-lang", "hi", "--until", "translate"]) == 0
    pivot = json.loads((out / "work/pivot.en.json").read_text(encoding="utf-8"))
    [shared] = pivot["attempts"]
    [pivot_unit] = pivot["units"]
    for language in ("ta", "bn"):
        target = json.loads((out / language / "segments.json").read_text(encoding="utf-8"))
        [unit] = target["units"]
        assert unit["pivot_unit_id"] == pivot_unit["unit_id"]
        assert unit["legs"][0]["attempt_ids"] == [shared["id"]]
        [final_attempt] = target["attempts"]
        assert final_attempt["id"] != shared["id"]
        assert unit["text"] == f"{language} 12 cm"


def test_gguf_is_a_local_attempt_and_uses_the_bodhan_prompt():
    plan = make_plan("en", "Measure 12 cm.")
    [planned] = plan.units
    log = AttemptLog()

    def respond(request):
        assert str(request.url) == "http://127.0.0.1:8080/v1/chat/completions"
        assert "authorization" not in request.headers
        body = json.loads(request.content)
        assert body["model"] == GGUF_MODEL
        assert body["messages"] == [
            {"role": "user", "content": f"Translate the following text into Hindi:\n\n{planned.payload}"},
        ]
        return httpx.Response(200, json={
            "model": GGUF_MODEL, "choices": [{
                "message": {"content": planned.payload.replace("Measure", "मापें")},
                "finish_reason": "stop",
            }],
        })

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        local = GGUFTranslate(client, gguf_endpoint("http://127.0.0.1:8080"))
        provider = IndicTranslateProvider(
            "local-first", Inference(log), client, {"BODHAN_TRANSLATE_KEY": "unused-key"},
            local, lambda: None, local_model=GGUF_MODEL,
        )
        result = translate_plan(plan, ("hi",), provider.translate)

    assert result.targets[0].units[0].text == "मापें 12 cm."
    [attempt] = log.attempts
    assert (attempt.service, attempt.model, attempt.backend) == ("indic-translate", GGUF_MODEL, "local")
    assert attempt.usage.cost_inr == 0


@pytest.mark.parametrize("error_message, hosted_expected", [
    ("Model failed to start.", False),
    ("CUDA out of memory.", True),
])
def test_gguf_hosted_fallback_requires_cuda_oom(error_message, hosted_expected):
    log = AttemptLog()
    requests = []

    def respond(request):
        requests.append(request.url.host)
        if request.url.host == "127.0.0.1":
            return httpx.Response(500, json={"error": {"message": error_message}})
        assert request.url.host == "api.bodhan.ai"
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "एक कोण।"}, "finish_reason": "stop"}],
        })

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        local = GGUFTranslate(client, gguf_endpoint("http://127.0.0.1:8080"))
        provider = IndicTranslateProvider(
            "local-first", Inference(log), client, {"BODHAN_TRANSLATE_KEY": "test-key"},
            local, lambda: None, local_model=GGUF_MODEL,
        )
        result = provider.translate("p001-b001", "en", "hi", "An angle.")

    if hosted_expected:
        assert requests == ["127.0.0.1", "api.bodhan.ai"]
        assert result.text == "एक कोण।"
        assert [attempt.model for attempt in log.attempts] == [GGUF_MODEL, "indic-translate"]
    else:
        assert requests == ["127.0.0.1"]
        assert result.text is None
        assert result.reason == "Local llama-server returned HTTP 500."


def test_gguf_checkpoint_selects_its_context_budget_and_adapter(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BODHAN_BACKEND", "local-first")
    monkeypatch.setenv("BODHAN_TRANSLATE_GGUF_URL", "http://127.0.0.1:8080")
    scans = tmp_path / "scans"
    scans.mkdir()
    with Image.new("RGB", (200, 300), "white") as image:
        image.save(scans / "page-1.png")

    def recognize(self, png, options):
        return {"markdown": "Measure the angle."}

    def gguf_translate(self, text, target):
        assert target == "hi"
        return ModelTranslation("कोण मापें।")

    monkeypatch.setattr(runner.LocalOCR, "__call__", recognize)
    monkeypatch.setattr(runner.GGUFTranslate, "__call__", gguf_translate)
    out = tmp_path / "out"

    assert main(["--input", str(scans), "--out", str(out), "--langs", "hi",
                 "--source-lang", "en", "--until", "translate"]) == 0
    plan = json.loads((out / "work/translation-plan.json").read_text(encoding="utf-8"))
    target = json.loads((out / "hi/segments.json").read_text(encoding="utf-8"))
    assert plan["context_limit"] == 4096
    assert target["units"][0]["text"] == "कोण मापें।"
    assert target["attempts"][0]["model"] == GGUF_MODEL
