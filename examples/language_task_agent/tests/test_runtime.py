from decimal import Decimal
import json
import httpx
import pytest

from bodhan_core.gateway import Gateway
from bodhan_core.runtime import Limits, Run, ToolError, decimal


def test_decimal_and_reservations(run):
    for bad in ("NaN", "Infinity", "-1"):
        with pytest.raises(ToolError):
            decimal(bad)
    run.limits.max_cost = Decimal("0.3")
    i = run.reserve(Decimal("0.2"), "model")
    with pytest.raises(ToolError, match="Insufficient"):
        run.reserve(Decimal("0.2"), "model")
    run.settle(i, Decimal("0.1"), "success")
    assert run.cost == Decimal("0.1")


def test_step_limit(run):
    run.limits.max_steps = 1
    run.step()
    with pytest.raises(ToolError, match="steps"):
        run.step()


def test_unknown_and_escape_references(run, tmp_path):
    with pytest.raises(ToolError):
        run.resolve("../../etc/passwd")
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    with pytest.raises(ToolError):
        run.register(outside, "input")
    link = run.input_root / "symlink.txt"
    link.symlink_to(outside)
    with pytest.raises(ToolError):
        run.register(link, "input")


def test_output_reuse_refused(run):
    with pytest.raises(ToolError, match="empty"):
        Run(run.input_root, run.output, Limits())


@pytest.mark.asyncio
async def test_success_cache_and_no_secret_trace(run, fake):
    gateway = Gateway(run, httpx.MockTransport(fake))
    kwargs = dict(
        model="indic-translate",
        url="https://api.bodhan.ai/translate",
        key_env="BODHAN_TRANSLATE_KEY",
        payload={"text": "hello", "target_language": "en"},
        reserve=Decimal("0.1"),
    )
    assert await gateway.request(**kwargs) == await gateway.request(**kwargs)
    assert len(fake.requests) == 1
    assert run.cost == Decimal("0.1")
    assert "mock-only-not-a-real-key" not in run.trace.read_text()
    await gateway.close()


@pytest.mark.asyncio
async def test_timeout_tombstone_keeps_cost_and_no_retry(run):
    seen = []

    def fail(request):
        seen.append(request)
        raise httpx.ReadTimeout("mock timeout")

    gateway = Gateway(run, httpx.MockTransport(fail))
    kwargs = dict(
        model="indic-translate",
        url="https://api.bodhan.ai/translate",
        key_env="BODHAN_TRANSLATE_KEY",
        payload={"text": "hello"},
        reserve=Decimal("0.1"),
    )
    with pytest.raises(ToolError, match="reserved cost"):
        await gateway.request(**kwargs)
    with pytest.raises(ToolError, match="not re-sent"):
        await gateway.request(**kwargs)
    assert len(seen) == 1 and run.cost == Decimal("0.1")
    await gateway.close()


@pytest.mark.asyncio
async def test_known_refusal_is_not_charged(run):
    gateway = Gateway(run, httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(ToolError):
        await gateway.request(
            model="indic-ocr",
            url="https://api.bodhan.ai/v1/chat/completions",
            key_env="BODHAN_OCR_KEY",
            payload={},
            reserve=Decimal("0.2"),
        )
    assert run.cost == 0
    await gateway.close()


@pytest.mark.asyncio
async def test_cost_guard_before_network(run, fake):
    run.limits.max_cost = Decimal("0")
    gateway = Gateway(run, httpx.MockTransport(fake))
    with pytest.raises(ToolError):
        await gateway.request(
            model="indic-ocr",
            url="https://api.bodhan.ai/v1/chat/completions",
            key_env="BODHAN_OCR_KEY",
            payload={},
            reserve=Decimal("0.2"),
        )
    assert not fake.requests
    await gateway.close()


def test_final_layout(run):
    ref = run.save_text("example", "summary", "planner_english")
    report = run.write_report("partial", "Mock evidence only", [ref])
    assert report["total_cost_inr"] == "0"
    assert {"result.md", "report.json", "trace.jsonl", "artifacts"} <= {
        p.name for p in run.output.iterdir()
    }
    assert json.loads((run.output / "report.json").read_text())["status"] == "partial"
