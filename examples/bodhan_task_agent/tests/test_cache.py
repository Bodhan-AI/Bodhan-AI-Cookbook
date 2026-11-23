from __future__ import annotations

import asyncio
import stat
from pathlib import Path

import httpx
import pytest
import respx
from conftest import make_settings, no_sleep, speech_wav

from bodhan_mcp.config import Settings
from bodhan_mcp.core.client import BodhanClient
from bodhan_mcp.exceptions import BodhanToolError
from bodhan_mcp.utils.cache import ResultCache, cache_key
from bodhan_mcp.utils.telemetry import call_scope, summarize

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
OCR_OK = {"id": "o1", "choices": [{"message": {"content": "पाठ"}}], "blocks": [{"label": "Paragraph", "text": "पाठ"}]}
OCR_TRUNCATED = {"error": {"message": "the recognizer did not return a usable transcription (order 1: truncated)"}}


def test_cache_key_is_stable_and_parameter_sensitive() -> None:
    assert cache_key("speak", {"a": 1, "b": 2}) == cache_key("speak", {"b": 2, "a": 1})
    assert cache_key("speak", {"a": 1}) != cache_key("ocr", {"a": 1})
    assert cache_key("speak", {"style": None}) != cache_key("speak", {"style": "news"})


def test_store_load_ttl_and_permissions(tmp_path: Path) -> None:
    now = [1_000_000.0]
    cache = ResultCache(tmp_path / "cache", ttl_s=60.0, clock=lambda: now[0])
    cache.store("speak", "ab12", ".wav", b"RIFF")
    path = tmp_path / "cache" / "speak" / "ab" / "ab12.wav"
    now[0] = path.stat().st_mtime + 30
    assert cache.load("speak", "ab12", ".wav") == b"RIFF"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "cache").stat().st_mode) == 0o700
    now[0] += 60
    assert cache.load("speak", "ab12", ".wav") is None
    assert ResultCache(None, 60.0).load("speak", "ab12", ".wav") is None
    nested = ResultCache(tmp_path / "missing" / "parent" / "cache", ttl_s=60.0)
    nested.store("ocr", "cd34", ".json", b"{}")
    assert nested.load("ocr", "cd34", ".json") == b"{}"


async def test_single_flight_shares_results_and_failures(tmp_path: Path) -> None:
    cache = ResultCache(None, 60.0)
    calls = []

    async def work() -> str:
        calls.append(1)
        await asyncio.sleep(0.01)
        return "done"

    results = await asyncio.gather(*(cache.single_flight("k", work) for _ in range(3)))
    assert [value for value, _ in results] == ["done"] * 3 and sum(sent for _, sent in results) == 1
    assert len(calls) == 1

    async def fail() -> str:
        calls.append(1)
        await asyncio.sleep(0.01)
        raise BodhanToolError("rate_limited", "busy", retryable=True)

    outcomes = await asyncio.gather(*(cache.single_flight("f", fail) for _ in range(2)), return_exceptions=True)
    assert all(isinstance(outcome, BodhanToolError) for outcome in outcomes) and len(calls) == 2


def _client_settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path, cache_dir=tmp_path / "cache")


async def test_repeat_speech_is_served_from_cache_at_no_cost(tmp_path: Path) -> None:
    settings = _client_settings(tmp_path)
    async with BodhanClient(settings, sleep=no_sleep) as client:
        with respx.mock(base_url=settings.base_url) as mock, call_scope() as calls:
            route = mock.post("/v1/audio/speech").mock(return_value=httpx.Response(200, content=speech_wav(0.2)))
            first = await client.speak("नमस्ते", "Kavya", "hi")
            second = await client.speak("नमस्ते", "Kavya", "hi")
            await client.speak("नमस्ते", "Kavya", "hi", style="news")
    assert route.call_count == 2 and first.audio == second.audio
    assert [record.cached for record in calls] == [False, True, False]
    usage = summarize(calls, 1.0)
    assert usage.model_calls == 2 and usage.cache_hits == 1


async def test_cache_survives_a_new_client(tmp_path: Path) -> None:
    settings = _client_settings(tmp_path)
    body = {"choices": [{"message": {"content": "नमस्ते"}, "finish_reason": "stop"}], "usage": {"completion_tokens": 3}}
    with respx.mock(base_url=settings.base_url) as mock:
        route = mock.post("/v1/chat/completions").mock(return_value=httpx.Response(200, json=body))
        for _ in range(2):
            async with BodhanClient(settings, sleep=no_sleep) as client:
                assert (await client.translate("Hello", "hi")).text == "नमस्ते"
    assert route.call_count == 1


async def test_truncated_and_failed_results_are_not_cached(tmp_path: Path) -> None:
    settings = _client_settings(tmp_path)
    cut = {"choices": [{"message": {"content": "आधा"}, "finish_reason": "length"}], "usage": {"completion_tokens": 9}}
    image = tmp_path / "p.png"
    image.write_bytes(PNG)
    async with BodhanClient(settings, sleep=no_sleep) as client:
        with respx.mock(base_url=settings.base_url) as mock:
            route = mock.post("/v1/chat/completions").mock(
                side_effect=[
                    httpx.Response(200, json=cut),
                    httpx.Response(200, json=cut),
                    httpx.Response(502, json=OCR_TRUNCATED),
                    httpx.Response(200, json=OCR_OK),
                ]
            )
            assert (await client.translate("Hello", "hi")).truncated
            assert (await client.translate("Hello", "hi")).truncated
            with pytest.raises(BodhanToolError):
                await client.ocr(image)
            assert (await client.ocr(image)).markdown == "पाठ"
    assert route.call_count == 4


async def test_corrupted_entry_is_a_miss(tmp_path: Path) -> None:
    settings = _client_settings(tmp_path)
    async with BodhanClient(settings, sleep=no_sleep) as client:
        with respx.mock(base_url=settings.base_url) as mock:
            route = mock.post("/v1/audio/speech").mock(return_value=httpx.Response(200, content=speech_wav(0.2)))
            await client.speak("a", "Amit", "hi")
            for entry in (tmp_path / "cache" / "speak").rglob("*.wav"):
                entry.write_bytes(b"garbage")
            await client.speak("a", "Amit", "hi")
    assert route.call_count == 2


async def test_identical_concurrent_requests_are_sent_once(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    image = tmp_path / "p.png"
    image.write_bytes(PNG)
    async with BodhanClient(settings, sleep=no_sleep) as client:
        with respx.mock(base_url=settings.base_url) as mock, call_scope() as calls:

            async def slow(_: httpx.Request) -> httpx.Response:
                await asyncio.sleep(0.2)
                return httpx.Response(200, json=OCR_OK)

            route = mock.post("/v1/chat/completions").mock(side_effect=slow)
            results = await asyncio.gather(*(client.ocr(image) for _ in range(3)))
    assert route.call_count == 1 and {r.markdown for r in results} == {"पाठ"}
    assert summarize(calls, 1.0).cache_hits == 2


def test_cache_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BODHAN_CACHE", "off")
    assert Settings.from_env().cache_dir is None
    monkeypatch.setenv("BODHAN_CACHE", "on")
    monkeypatch.setenv("BODHAN_CACHE_TTL_DAYS", "2")
    loaded = Settings.from_env()
    assert loaded.cache_dir is not None and loaded.cache_dir.is_absolute() and loaded.cache_ttl_s == 2 * 86_400
