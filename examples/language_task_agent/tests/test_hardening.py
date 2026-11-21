import asyncio
import json
from decimal import Decimal

import httpx
import pytest

from bodhan_agent.runner import execute
from bodhan_core.gateway import Gateway
from bodhan_core.runtime import Limits, Run, ToolError
from conftest import input_ref
from test_agent import tool_response


@pytest.mark.asyncio
async def test_persistent_cache_avoids_paid_rerun(run, fake):
    kwargs = dict(
        model="indic-translate",
        url="https://api.bodhan.ai/translate",
        key_env="BODHAN_TRANSLATE_KEY",
        payload={"text": "cache me", "target_language": "en"},
        reserve=Decimal("0.1"),
    )
    gateway = Gateway(run, httpx.MockTransport(fake))
    result = await gateway.request(**kwargs)
    second = Run(run.input_root, run.output.parent / "second_run", Limits())
    another = Gateway(second, httpx.MockTransport(fake))
    assert await another.request(**kwargs) == result
    assert len(fake.requests) == 1 and second.cost == 0
    await gateway.close()
    await another.close()


@pytest.mark.asyncio
async def test_uncertain_call_survives_restart(run):
    def timeout(request):
        raise httpx.ReadTimeout("fixture timeout")

    kwargs = dict(
        model="indic-translate",
        url="https://api.bodhan.ai/translate",
        key_env="BODHAN_TRANSLATE_KEY",
        payload={"text": "pending"},
        reserve=Decimal("0.1"),
    )
    gateway = Gateway(run, httpx.MockTransport(timeout))
    with pytest.raises(ToolError):
        await gateway.request(**kwargs)
    second = Run(run.input_root, run.output.parent / "second_run", Limits())
    another = Gateway(
        second, httpx.MockTransport(lambda r: pytest.fail("Duplicate billed-uncertain request"))
    )
    with pytest.raises(ToolError, match="not re-sent"):
        await another.request(**kwargs)
    assert second.cost == 0
    await gateway.close()
    await another.close()


@pytest.mark.asyncio
async def test_total_call_timeout_not_only_socket_timeout(run):
    async def slow(request):
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={})

    run.limits.call_timeout = 0.01
    gateway = Gateway(run, httpx.MockTransport(slow))
    with pytest.raises(ToolError, match="timed out"):
        await gateway.request(
            model="indic-ocr",
            url="https://api.bodhan.ai/v1/chat/completions",
            key_env="BODHAN_OCR_KEY",
            payload={},
            reserve=Decimal("0.2"),
        )
    assert run.cost == Decimal("0.2")
    await gateway.close()


@pytest.mark.asyncio
async def test_credit_exhaustion_429_does_not_loop_retry(run):
    requests = []

    def exhausted(request):
        requests.append(request)
        return httpx.Response(429, json={"error": {"code": "insufficient_credit"}})

    gateway = Gateway(run, httpx.MockTransport(exhausted))
    with pytest.raises(ToolError):
        await gateway.request(
            model="indic-ocr",
            url="https://api.bodhan.ai/v1/chat/completions",
            key_env="BODHAN_OCR_KEY",
            payload={},
            reserve=Decimal("0.2"),
        )
    assert len(requests) == 1 and run.cost == 0
    await gateway.close()


@pytest.mark.asyncio
async def test_rate_limit_known_refusal_bounded_backoff(run, monkeypatch):
    requests, sleeps = [], []

    async def sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("bodhan_core.gateway.asyncio.sleep", sleep)

    def limited(request):
        requests.append(request)
        if len(requests) < 3:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={"ok": True})

    gateway = Gateway(run, httpx.MockTransport(limited))
    result = await gateway.request(
        model="indic-ocr",
        url="https://api.bodhan.ai/v1/chat/completions",
        key_env="BODHAN_OCR_KEY",
        payload={},
        reserve=Decimal("0.2"),
    )
    assert result["ok"] and sleeps == [1.0, 2.0] and run.cost == Decimal("0.2")
    await gateway.close()


@pytest.mark.asyncio
async def test_planner_cannot_invent_artifact(tools, run, fake):
    input_ref(run)
    fake.planner = lambda r: tool_response(
        "finish",
        {
            "status": "completed",
            "explanation": "Done.",
            "outputs": ["file_invented"],
            "assumptions": [],
        },
        1,
    )
    report = await execute("Translate this.", run, tools.gateway)
    assert report["status"] == "failed" and "missing_file" in report["explanation"]


@pytest.mark.asyncio
async def test_llm_context_does_not_contain_media_bytes(tools, run, fake):
    path = run.input_root / "page.png"
    from PIL import Image

    Image.new("RGB", (20, 20), "white").save(path)
    count = 0

    def planner(request):
        nonlocal count
        count += 1
        assert "data:image" not in request.content.decode()
        assert "base64" not in request.content.decode()
        if count == 1:
            manifest = json.loads(
                json.loads(request.content)["messages"][-1]["content"].split("\n", 1)[1]
            )
            return tool_response("ocr", {"source": manifest["files"][0]["ref"]}, 1)
        data = json.loads(json.loads(request.content)["messages"][-1]["content"].split("\n", 1)[1])
        return tool_response(
            "finish",
            {
                "status": "completed",
                "explanation": "OCR complete, mock-only.",
                "outputs": [data["ref"]],
                "assumptions": [],
            },
            2,
        )

    fake.planner = planner
    report = await execute("Extract the page.", run, tools.gateway)
    assert report["status"] == "completed"


@pytest.mark.asyncio
async def test_no_empty_success_report(tools, run, fake):
    input_ref(run)
    fake.planner = lambda r: tool_response(
        "finish",
        {
            "status": "completed",
            "explanation": "I finished without evidence.",
            "outputs": [],
            "assumptions": [],
        },
        1,
    )
    report = await execute("Produce a summary.", run, tools.gateway)
    assert report["status"] == "failed"
    assert "missing_deliverable" in report["explanation"]


@pytest.mark.asyncio
async def test_english_latin_names_and_provider_extensions(tools, run, fake, monkeypatch):
    from bodhan_agent.runner import english

    assert english("José visits the café.")
    assert not english("வணக்கம்")
    assert (await tools.call("write_english", {"text": "José visits the café."}))["ok"]
    monkeypatch.setenv("LLM_TOKEN_LIMIT_FIELD", "max_completion_tokens")
    input_ref(run)

    def planner(request):
        payload = json.loads(request.content)
        assert "max_completion_tokens" in payload and "max_tokens" not in payload
        return tool_response(
            "finish",
            {
                "status": "failed",
                "explanation": "Compatibility mock only.",
                "outputs": [],
                "assumptions": [],
            },
            1,
        )

    fake.planner = planner
    report = await execute("Check configuration.", run, tools.gateway)
    assert report["explanation"] == "Compatibility mock only."


@pytest.mark.asyncio
async def test_failed_audio_verification_stays_in_ledger(tools, run, fake, monkeypatch):
    ref = run.save_text("The class starts at nine.", "text", "planner_english")

    async def fail(*args, **kwargs):
        from bodhan_core.runtime import ToolError

        raise ToolError("temporary_failure", "Verification unavailable.")

    monkeypatch.setattr(tools, "transcribe", fail)
    result = await tools.call("synthesize", {"source": ref, "language": "en"})
    assert not result["ok"]
    assert any(c["kind"] == "audio" and not c["checked"] and not c["passed"] for c in run.checks)


@pytest.mark.asyncio
async def test_translation_table_shape_checks_actual_output(tools, run, fake):
    ref = run.save_text("<table><tr><td>Hello</td><td>World</td></tr></table>", "table", "input")
    fake.translation = (
        lambda body: "Hi</td><td>Unexpected" if body["target_language"] != "en" else body["text"]
    )
    result = await tools.call("translate", {"source": ref, "target_language": "mr"})
    assert not result["ok"]
    assert result["error"]["code"] == "table_structure_changed"


@pytest.mark.asyncio
async def test_explicit_token_parameter_fallback_only_once(run, monkeypatch):
    input_ref(run)
    requests = []

    def provider(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Unsupported parameter: max_tokens. Use max_completion_tokens instead."
                    }
                },
            )
        assert "max_completion_tokens" in payload and "max_tokens" not in payload
        return httpx.Response(
            200,
            json=tool_response(
                "finish",
                {
                    "status": "failed",
                    "explanation": "Compatibility checked.",
                    "outputs": [],
                    "assumptions": [],
                },
                1,
            ),
        )

    report = await execute("Check endpoint.", run, Gateway(run, httpx.MockTransport(provider)))
    assert report["explanation"] == "Compatibility checked."
    assert len(requests) == 2 and run.calls[0]["actual_inr"] == "0"
    assert "planner_token_parameter_switch" in run.trace.read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,message",
    [(400, "Invalid schema."), (500, "max_tokens unsupported"), (400, "max_tokens value too high")],
)
async def test_ambiguous_error_never_switches_token_parameter(run, status, message):
    input_ref(run)
    requests = []

    def provider(request):
        requests.append(request)
        return httpx.Response(status, json={"error": {"message": message}})

    report = await execute("Check endpoint.", run, Gateway(run, httpx.MockTransport(provider)))
    assert report["status"] == "failed" and len(requests) == 1
    assert "planner_token_parameter_switch" not in run.trace.read_text()


@pytest.mark.asyncio
async def test_rejected_fallback_does_not_try_third_parameter(run):
    input_ref(run)
    requests = []

    def provider(request):
        requests.append(request)
        field = "max_tokens" if len(requests) == 1 else "max_completion_tokens"
        return httpx.Response(400, json={"error": {"message": "Unsupported parameter: " + field}})

    report = await execute("Check endpoint.", run, Gateway(run, httpx.MockTransport(provider)))
    assert report["status"] == "failed" and len(requests) == 2
    assert all(call["actual_inr"] == "0" for call in run.calls)


@pytest.mark.asyncio
async def test_list_enveloped_provider_quota_error_preserves_diagnostics(run):
    def provider(request):
        return httpx.Response(
            429,
            json=[
                {"error": {"message": "Daily token quota reached. Try again after quota reset."}}
            ],
        )

    gateway = Gateway(run, httpx.MockTransport(provider))
    with pytest.raises(ToolError, match="Daily token quota reached"):
        await gateway.request(
            model="planner:test",
            url="https://mock-planner.example/v1/chat/completions",
            key_env="LLM_API_KEY",
            payload={"model": "test"},
            reserve=Decimal("0"),
        )
    trace = [json.loads(row) for row in run.trace.read_text().splitlines()]
    rejection = next(row for row in trace if row["kind"] == "api_rejection")
    assert rejection["http_status"] == 429 and rejection["retry_after"] is None
    assert "quota reset" in rejection["detail"]


@pytest.mark.asyncio
async def test_optional_planner_pacing_and_no_indic_quotation(tools, run, fake, monkeypatch):
    monkeypatch.setenv("LLM_MIN_INTERVAL_SECONDS", "0.01")
    gateway = Gateway(
        run, httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}))
    )
    for i in range(2):
        await gateway.request(
            model="planner:test",
            url="https://mock-planner.example/v1/chat/completions",
            key_env="LLM_API_KEY",
            payload={"i": i},
            reserve=Decimal("0"),
        )
    assert "planner_pacing" in run.trace.read_text()
    result = await tools.call("write_english", {"text": "Source quote: పాఠశాల"})
    assert not result["ok"] and result["error"]["code"] == "planner_language_violation"


@pytest.mark.asyncio
async def test_long_speech_chunk_is_never_marked_verified(tools, run, fake):
    from conftest import wav_bytes

    previous = fake.speech_audio
    fake.speech_audio = lambda text: wav_bytes(31)
    ref = run.save_text("An original short English sentence.", "source", "input")
    result = await tools.call("synthesize", {"source": ref, "language": "en"})
    assert not result["ok"] and result["error"]["code"] == "speech_chunk_too_long"
    assert any(c["kind"] == "audio" and not c["passed"] for c in run.checks)
    fake.speech_audio = previous

@pytest.mark.asyncio
async def test_optional_planner_reasoning_and_token_cap_are_sent(tools, run, fake, monkeypatch):
    input_ref(run)
    monkeypatch.setenv("LLM_REASONING_EFFORT", "medium")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "4096")
    fake.planner = lambda request: tool_response("finish", {
        "status": "partial", "explanation": "Configuration fixture only.",
        "outputs": [], "assumptions": [],
    }, 1)
    report = await execute("Inspect configuration.", run, tools.gateway)
    assert report["status"] == "partial"
    payload = json.loads(fake.requests[0].content)
    assert payload["reasoning_effort"] == "medium"
    assert payload["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_larger_planner_cap_is_reserved_before_dispatch(tools, run, fake, monkeypatch):
    input_ref(run)
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "4096")
    monkeypatch.setenv("LLM_INPUT_INR_PER_MILLION", "0")
    monkeypatch.setenv("LLM_OUTPUT_INR_PER_MILLION", "1000")
    run.limits.max_cost = Decimal("2")
    fake.planner = lambda request: tool_response("finish", {
        "status": "partial", "explanation": "Configuration fixture only.",
        "outputs": [], "assumptions": [],
    }, 1)
    report = await execute("Inspect configuration.", run, tools.gateway)
    assert report["status"] == "failed"
    assert "cost_budget" in report["explanation"]
    assert not fake.requests


@pytest.mark.asyncio
@pytest.mark.parametrize("name,value", [
    ("LLM_MAX_OUTPUT_TOKENS", "0"),
    ("LLM_MAX_OUTPUT_TOKENS", "abc"),
    ("LLM_MAX_OUTPUT_TOKENS", "65537"),
    ("LLM_MAX_OUTPUT_TOKENS", "-1"),
    ("LLM_REASONING_EFFORT", "impossible"),
])
async def test_invalid_planner_controls_fail_before_dispatch(tools, run, fake, monkeypatch, name, value):
    input_ref(run)
    monkeypatch.setenv(name, value)
    report = await execute("Inspect configuration.", run, tools.gateway)
    assert report["status"] == "failed"
    assert "planner_config" in report["explanation"]
    assert not fake.requests
