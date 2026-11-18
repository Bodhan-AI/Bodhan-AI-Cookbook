"""Tests for retry safety across all 4 Bodhan tools:
Ensures that once a 2xx response is received from api.bodhan.ai,
subsequent processing failures NEVER trigger a retry, making exactly 1 HTTP request
and recording exactly 1 billed ledger line.
Also tests per-run-id spend tracking and global budget cap enforcement.
"""

import json
import httpx
import pytest

from bodhan_mcp.common import (
    check_global_cap_allowed,
    count_requests_from_ledger,
    total_spend_inr,
)
from bodhan_mcp.tools.translate import IndicTranslateTool
from bodhan_mcp.tools.ocr import IndicOcrTool
from bodhan_mcp.tools.transcribe import IndicTranscribeTool
from bodhan_mcp.tools.speak import IndicSpeakTool


@pytest.mark.asyncio
async def test_translate_exception_after_200_no_retry(tmp_path, monkeypatch):
    """If translate API returns HTTP 200 with invalid JSON, tool must not retry."""
    ledger_file = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("BODHAN_RECORD_MOCK_SPEND", "1")
    request_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        # HTTP 200 OK from server, but invalid/corrupted JSON payload
        return httpx.Response(200, content=b"Corrupted Non-JSON Body {{{{")

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    tool = IndicTranslateTool(
        client=client,
        cache_dir=tmp_path / "cache",
        api_key="fake-key",
        run_id="test_run_tr",
        ledger_path=ledger_file,
    )

    try:
        res = await tool.translate("Hello world", target_language="hi")
        assert not res.success
        assert res.error is not None
        assert res.error.code == "UNKNOWN_BILLED"
        # Must make EXACTLY 1 HTTP request (no retry after 200)
        assert request_count == 1

        # Must record EXACTLY 1 billed ledger line with status completed
        lines = [
            json.loads(line)
            for line in ledger_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(lines) == 1
        assert lines[0]["status"] == "completed"
        assert lines[0]["http_status"] == 200
        assert lines[0]["model"] == "indic-translate"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_ocr_exception_after_200_no_retry(tmp_path, monkeypatch):
    """If OCR API returns HTTP 200 with invalid JSON, tool must not retry."""
    ledger_file = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("BODHAN_RECORD_MOCK_SPEND", "1")
    request_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, content=b"Invalid JSON response")

    dummy_img = tmp_path / "sample.png"
    dummy_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    tool = IndicOcrTool(
        client=client,
        cache_dir=tmp_path / "cache",
        api_key="fake-key",
        run_id="test_run_ocr",
        ledger_path=ledger_file,
    )

    try:
        res = await tool.parse_document(dummy_img)
        assert not res.success
        assert res.error is not None
        assert res.error.code == "UNKNOWN_BILLED"
        # Must make EXACTLY 1 HTTP request
        assert request_count == 1

        lines = [
            json.loads(line)
            for line in ledger_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(lines) == 1
        assert lines[0]["status"] == "completed"
        assert lines[0]["http_status"] == 200
        assert lines[0]["model"] == "indic-ocr"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_transcribe_exception_after_200_no_retry(tmp_path, monkeypatch):
    """If transcribe API returns HTTP 200 with invalid JSON, tool must not retry."""
    ledger_file = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("BODHAN_RECORD_MOCK_SPEND", "1")
    request_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, content=b"{not: valid json}")

    dummy_wav = tmp_path / "sample.wav"
    dummy_wav.write_bytes(b"RIFF" + b"\x00" * 50)

    monkeypatch.setattr(
        "bodhan_mcp.tools.transcribe.probe_audio_duration",
        lambda p, b: (2.0, None),
    )
    monkeypatch.setattr(
        "bodhan_mcp.tools.transcribe.extract_chunk_wav_bytes",
        lambda p, b, s, e: b"RIFFchunk",
    )

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    tool = IndicTranscribeTool(
        client=client,
        cache_dir=tmp_path / "cache",
        api_key="fake-key",
        run_id="test_run_tr",
        ledger_path=ledger_file,
    )

    try:
        res = await tool.transcribe(dummy_wav, language="hi")
        assert not res.success
        assert res.error is not None
        assert res.error.code == "UNKNOWN_BILLED"
        # Must make EXACTLY 1 HTTP request
        assert request_count == 1

        lines = [
            json.loads(line)
            for line in ledger_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(lines) == 1
        assert lines[0]["status"] == "completed"
        assert lines[0]["http_status"] == 200
        assert lines[0]["model"] == "indic-transcribe"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_speak_exception_after_200_no_retry(tmp_path, monkeypatch):
    """If speak API returns HTTP 200 with empty or corrupted audio, tool must not retry."""
    ledger_file = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("BODHAN_RECORD_MOCK_SPEND", "1")
    request_count = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        # HTTP 200 with empty body
        return httpx.Response(200, content=b"")

    client = httpx.AsyncClient(transport=httpx.MockTransport(mock_handler))
    tool = IndicSpeakTool(
        client=client,
        cache_dir=tmp_path / "cache",
        api_key="fake-key",
        run_id="test_run_sp",
        ledger_path=ledger_file,
    )

    try:
        res = await tool.speak(
            input_text="Good morning",
            voice="Kavya",
            language="hi",
            output_path=tmp_path / "out.wav",
        )
        assert not res.success
        assert res.error is not None
        assert res.error.code in ("EMPTY_AUDIO", "UNKNOWN_BILLED")
        # Must make EXACTLY 1 HTTP request
        assert request_count == 1

        lines = [
            json.loads(line)
            for line in ledger_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(lines) == 1
        assert lines[0]["status"] == "completed"
        assert lines[0]["http_status"] == 200
        assert lines[0]["model"] == "indic-speak"
    finally:
        await client.aclose()


def test_per_run_id_spend_tracking(tmp_path):
    """total_spend_inr must filter by run_id when specified and count requests correctly."""
    ledger_file = tmp_path / "spend_ledger.jsonl"

    entries = [
        {
            "call_id": "c1",
            "run_id": "run_alpha",
            "timestamp": "2026-11-17T10:00:00",
            "model": "indic-translate",
            "billed_units": 1000,
            "estimated_cost_inr": 0.02,
            "status": "completed",
            "cache_hit": False,
        },
        {
            "call_id": "c2",
            "run_id": "run_alpha",
            "timestamp": "2026-11-17T10:01:00",
            "model": "indic-ocr",
            "billed_units": 1,
            "estimated_cost_inr": 0.20,
            "status": "completed",
            "cache_hit": False,
        },
        {
            "call_id": "c3",
            "run_id": "run_beta",
            "timestamp": "2026-11-17T10:02:00",
            "model": "indic-transcribe",
            "billed_units": 30,
            "estimated_cost_inr": 0.05,
            "status": "completed",
            "cache_hit": False,
        },
        {
            "call_id": "c4",
            "run_id": "run_alpha",
            "timestamp": "2026-11-17T10:03:00",
            "model": "indic-ocr",
            "billed_units": 0,
            "estimated_cost_inr": 0.0,
            "status": "failed",
            "cache_hit": False,
        },
    ]

    with open(ledger_file, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")

    # Alpha spend = 0.02 + 0.20 = 0.22 (ignoring failed c4)
    assert total_spend_inr(run_id="run_alpha", ledger_path=ledger_file) == 0.22
    # Beta spend = 0.05
    assert total_spend_inr(run_id="run_beta", ledger_path=ledger_file) == 0.05
    # Total spend across all runs = 0.27
    assert total_spend_inr(run_id=None, ledger_path=ledger_file) == 0.27

    # Count requests from ledger
    assert (
        count_requests_from_ledger(run_id="run_alpha", ledger_path=ledger_file) == 3
    )  # c1, c2, c4
    assert count_requests_from_ledger(run_id="run_beta", ledger_path=ledger_file) == 1  # c3
    assert count_requests_from_ledger(run_id=None, ledger_path=ledger_file) == 4


def test_global_cap_enforcement(tmp_path, monkeypatch):
    """check_global_cap_allowed refuses calls when full ledger exceeds BODHAN_GLOBAL_CAP_INR."""
    ledger_file = tmp_path / "spend_ledger.jsonl"
    monkeypatch.setenv("BODHAN_GLOBAL_CAP_INR", "0.50")

    # Seed ledger with 0.45 INR
    entry = {
        "call_id": "c1",
        "run_id": "run_old",
        "timestamp": "2026-11-17T10:00:00",
        "model": "indic-ocr",
        "billed_units": 2,
        "estimated_cost_inr": 0.45,
        "status": "completed",
        "cache_hit": False,
    }
    ledger_file.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    # 1. A small call that fits (0.04) is permitted: 0.45 + 0.04 = 0.49 <= 0.50
    err_ok = check_global_cap_allowed(0.04, ledger_path=ledger_file)
    assert err_ok is None

    # 2. A call that exceeds (0.10): 0.45 + 0.10 = 0.55 > 0.50 -> refused!
    err_block = check_global_cap_allowed(0.10, ledger_path=ledger_file)
    assert err_block is not None
    assert err_block.code == "GLOBAL_CAP_EXCEEDED"
    assert "0.50" in err_block.message
