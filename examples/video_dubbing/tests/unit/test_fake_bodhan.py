"""
Unit tests verifying FakeBodhanTransport behavior and fault injection.
"""

import httpx
import pytest
from fake_bodhan.server import FakeBodhanTransport


@pytest.mark.asyncio
async def test_fake_bodhan_transcribe():
    transport = FakeBodhanTransport()
    client = httpx.AsyncClient(base_url="https://api.bodhan.ai/v1", transport=transport)

    resp = await client.post(
        "/audio/transcriptions",
        data={"model": "indic-transcribe", "response_format": "verbose_json"},
        files={"file": ("test.wav", b"fake-wav-bytes", "audio/wav")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "text" in data
    assert "segments" in data
    assert len(data["segments"]) > 0


@pytest.mark.asyncio
async def test_fake_bodhan_translate():
    transport = FakeBodhanTransport()
    client = httpx.AsyncClient(base_url="https://api.bodhan.ai/v1", transport=transport)

    resp = await client.post(
        "/chat/completions",
        json={
            "model": "indic-translate",
            "messages": [{"role": "user", "content": "Welcome"}],
            "target_language_code": "hi",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    assert "स्वागत" in content


@pytest.mark.asyncio
async def test_fake_bodhan_speak():
    transport = FakeBodhanTransport()
    client = httpx.AsyncClient(base_url="https://api.bodhan.ai/v1", transport=transport)

    resp = await client.post(
        "/audio/speech",
        json={
            "model": "indic-speak",
            "input": "नमस्ते",
            "voice": "Kavya",
            "instructions": '{"lang": "hi"}',
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    assert len(resp.content) > 100


@pytest.mark.asyncio
async def test_fake_bodhan_fault_injection():
    transport = FakeBodhanTransport(failure_injection={"rate_limit_transcribe": True})
    client = httpx.AsyncClient(base_url="https://api.bodhan.ai/v1", transport=transport)

    resp = await client.post("/audio/transcriptions")
    assert resp.status_code == 429
    assert resp.headers.get("Retry-After") == "1"
