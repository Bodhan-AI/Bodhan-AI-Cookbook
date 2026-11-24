"""Tests for live-call safety guard and spend ledger accounting."""

import json
from pathlib import Path
import httpx
import pytest

from bodhan_mcp.common import (
    StructuredError,
    check_live_calls_allowed,
    is_mock_transport,
    log_spend,
    total_spend_inr,
)
from bodhan_mcp.tools.translate import IndicTranslateTool
from bodhan_mcp.tools.ocr import IndicOcrTool
from bodhan_mcp.tools.transcribe import IndicTranscribeTool
from bodhan_mcp.tools.speak import IndicSpeakTool


@pytest.mark.asyncio
async def test_live_call_allowed_when_env_unset(monkeypatch, tmp_path):
    """When BODHAN_ALLOW_LIVE is unset, calls are allowed by default (tested without network)."""
    monkeypatch.delenv("BODHAN_ALLOW_LIVE", raising=False)
    real_client = httpx.AsyncClient()
    try:
        url = "https://api.bodhan.ai/v1/chat/completions"
        assert check_live_calls_allowed(url, real_client) is None
    finally:
        await real_client.aclose()

    # Verify a tool call succeeds end-to-end with fake transport (no network) when env is unset
    def fake_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "नमस्ते दुनिया"},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16},
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(fake_handler))
    try:
        t_tool = IndicTranslateTool(
            client=mock_client,
            cache_dir=tmp_path / "cache_tr_unset",
            api_key="fake-key",
            ledger_path=tmp_path / "ledger_unset.jsonl",
        )
        t_res = await t_tool.translate("Hello world", target_language="hi")
        assert t_res.success is True
        assert t_res.translated_text == "नमस्ते दुनिया"
    finally:
        await mock_client.aclose()


@pytest.mark.asyncio
async def test_live_call_refused_when_env_zero_or_false(monkeypatch):
    """Real HTTP client to api.bodhan.ai with BODHAN_ALLOW_LIVE=0 or false must be refused."""
    for disabled_val in ("0", "false", "FALSE", " 0 "):
        monkeypatch.setenv("BODHAN_ALLOW_LIVE", disabled_val)
        client = httpx.AsyncClient()
        try:
            url = "https://api.bodhan.ai/v1/chat/completions"
            err = check_live_calls_allowed(url, client)
            assert err is not None
            assert isinstance(err, StructuredError)
            assert err.code == "LIVE_CALLS_DISABLED"
            assert "disabled" in err.message.lower()
            assert not err.retryable
        finally:
            await client.aclose()


@pytest.mark.asyncio
async def test_live_call_allowed_with_env_var(monkeypatch):
    """When BODHAN_ALLOW_LIVE=1 is explicitly set, check_live_calls_allowed permits real HTTP."""
    monkeypatch.setenv("BODHAN_ALLOW_LIVE", "1")
    client = httpx.AsyncClient()
    try:
        url = "https://api.bodhan.ai/v1/chat/completions"
        err = check_live_calls_allowed(url, client)
        assert err is None
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_mock_transport_always_allowed(monkeypatch):
    """MockTransport (used in test suite) must never be blocked even when BODHAN_ALLOW_LIVE=0."""
    monkeypatch.setenv("BODHAN_ALLOW_LIVE", "0")
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"text": "ok"}))
    client = httpx.AsyncClient(transport=transport)
    try:
        assert is_mock_transport(client) is True
        url = "https://api.bodhan.ai/v1/chat/completions"
        err = check_live_calls_allowed(url, client)
        assert err is None
    finally:
        await client.aclose()


def test_non_bodhan_url_ignored(monkeypatch):
    """Non-Bodhan endpoints are never intercepted by the Bodhan live guard."""
    monkeypatch.setenv("BODHAN_ALLOW_LIVE", "0")
    client = httpx.AsyncClient()
    err = check_live_calls_allowed("https://example.com/api", client)
    assert err is None


@pytest.mark.asyncio
async def test_tools_enforce_live_guard(monkeypatch, tmp_path):
    """All tools must return LIVE_CALLS_DISABLED when BODHAN_ALLOW_LIVE=0 with a real client."""
    monkeypatch.setenv("BODHAN_ALLOW_LIVE", "0")
    real_client = httpx.AsyncClient()

    try:
        # 1. Translate
        t_tool = IndicTranslateTool(
            client=real_client,
            cache_dir=tmp_path / "cache_tr",
            api_key="fake-key",
        )
        t_res = await t_tool.translate("Hello world", target_language="hi")
        assert not t_res.success
        assert t_res.error is not None
        assert t_res.error.code == "LIVE_CALLS_DISABLED"

        # 2. OCR
        dummy_img = tmp_path / "dummy.png"
        dummy_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
        ocr_tool = IndicOcrTool(
            client=real_client,
            cache_dir=tmp_path / "cache_ocr",
            api_key="fake-key",
        )
        ocr_res = await ocr_tool.parse_document(dummy_img)
        assert not ocr_res.success
        assert ocr_res.error is not None
        assert ocr_res.error.code == "LIVE_CALLS_DISABLED"

        # 3. Transcribe
        dummy_wav = tmp_path / "dummy.wav"
        dummy_wav.write_bytes(b"RIFF" + b"\x00" * 50)
        tr_tool = IndicTranscribeTool(
            client=real_client,
            cache_dir=tmp_path / "cache_transcribe",
            api_key="fake-key",
        )
        # Mock extract_chunk_wav_bytes and probe_audio_duration so we only test the HTTP guard
        monkeypatch.setattr(
            "bodhan_mcp.tools.transcribe.probe_audio_duration",
            lambda p, b: (2.0, None),
        )
        monkeypatch.setattr(
            "bodhan_mcp.tools.transcribe.extract_chunk_wav_bytes",
            lambda p, b, s, e: b"RIFFchunk",
        )
        tr_res = await tr_tool.transcribe(dummy_wav, language="hi")
        assert not tr_res.success
        assert tr_res.error is not None
        assert tr_res.error.code == "LIVE_CALLS_DISABLED"

        # 4. Speak
        sp_tool = IndicSpeakTool(
            client=real_client,
            cache_dir=tmp_path / "cache_sp",
            api_key="fake-key",
        )
        sp_res = await sp_tool.speak(
            input_text="Good morning",
            voice="Kavya",
            language="hi",
            output_path=tmp_path / "out.wav",
        )
        assert not sp_res.success
        assert sp_res.error is not None
        assert sp_res.error.code == "LIVE_CALLS_DISABLED"
    finally:
        await real_client.aclose()


def test_spend_ledger_logging_and_total(tmp_path):
    """Spend ledger records paid events, logs cache hits as Rs 0, and computes total spend."""
    ledger = tmp_path / "test_ledger.jsonl"

    # 1. Non-existent ledger returns 0.0
    assert total_spend_inr(ledger) == 0.0

    # 2. Log paid translate call
    e1 = log_spend(
        model="indic-translate",
        billed_units=18,
        estimated_cost_inr=0.00036,
        caller="indic_translate",
        cache_hit=False,
        ledger_path=ledger,
    )
    assert e1["model"] == "indic-translate"
    assert e1["estimated_cost_inr"] == 0.00036
    assert e1["cache_hit"] is False

    # 3. Log cache hit (must record Rs 0.00)
    e2 = log_spend(
        model="indic-translate",
        billed_units=0,
        estimated_cost_inr=0.00036,  # Even if caller passes cost, cache_hit forces Rs 0.0
        caller="indic_translate",
        cache_hit=True,
        ledger_path=ledger,
    )
    assert e2["estimated_cost_inr"] == 0.0
    assert e2["cache_hit"] is True

    # 4. Log paid OCR call
    e3 = log_spend(
        model="indic-ocr",
        billed_units=1,
        estimated_cost_inr=0.20,
        caller="indic_ocr",
        cache_hit=False,
        ledger_path=ledger,
    )
    assert e3["estimated_cost_inr"] == 0.20

    # 5. Read back ledger file lines
    lines = [
        json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(lines) == 3
    assert lines[0]["model"] == "indic-translate"
    assert lines[1]["cache_hit"] is True
    assert lines[1]["estimated_cost_inr"] == 0.0
    assert lines[2]["model"] == "indic-ocr"
    assert lines[2]["estimated_cost_inr"] == 0.20

    # 6. Verify total_spend_inr
    expected_total = round(0.00036 + 0.0 + 0.20, 6)
    assert total_spend_inr(ledger) == expected_total


def test_each_run_report_cost_equals_ledger_entries():
    """Verify that every historical/live run's report.json total_cost_inr equals the sum of its ledger entries."""
    repo_root = Path(__file__).resolve().parent.parent
    ledger_path = repo_root / ".cache" / "spend_ledger.jsonl"
    if not ledger_path.exists():
        pytest.skip("spend_ledger.jsonl not found")

    ledger_entries = [
        json.loads(line)
        for line in ledger_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    run_mappings = {
        "run1b": "run_a68f95f4",
        "run2": "run_87d092a3",
        "run3": None,  # Terminated pre-flight, 0 entries
    }

    for run_dir_name, run_id in run_mappings.items():
        report_file = repo_root / "out" / run_dir_name / "report.json"
        if not report_file.exists():
            continue

        report = json.loads(report_file.read_text(encoding="utf-8"))
        report_cost = float(report.get("total_cost_inr", 0.0))

        if run_id is None:
            ledger_sum = 0.0
        else:
            matching_entries = [
                e
                for e in ledger_entries
                if e.get("run_id") == run_id and e.get("status") != "failed"
            ]
            ledger_sum = sum(float(e.get("estimated_cost_inr", 0.0)) for e in matching_entries)

        assert abs(report_cost - ledger_sum) < 1e-5, (
            f"Run '{run_dir_name}' report cost Rs {report_cost:.6f} does not match "
            f"ledger sum Rs {ledger_sum:.6f} for run_id '{run_id}'"
        )
