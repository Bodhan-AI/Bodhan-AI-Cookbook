"""CLI argument handling and the OpenAI-compatible client against a mock HTTP endpoint."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from bodhan_agent.cli import build_parser, main, split_files
from bodhan_agent.llm import LLMError, OpenAICompatLLM

TOOLS = [{"type": "function", "function": {"name": "finish", "description": "End.",
                                           "parameters": {"type": "object", "properties": {}}}}]


def completion(message: dict[str, Any]) -> dict[str, Any]:
    return {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", **message}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}}


def llm_with(handler: Any) -> tuple[OpenAICompatLLM, list[dict[str, Any]]]:
    bodies: list[dict[str, Any]] = []

    def record(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return handler(request, len(bodies))

    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    llm = OpenAICompatLLM(base_url="http://llm.test/v1", model="m", api_key="k", http_client=client, max_retries=2)
    return llm, bodies


def test_native_tool_calls_are_parsed() -> None:
    llm, _ = llm_with(lambda req, n: httpx.Response(200, json=completion({
        "content": "Done.", "tool_calls": [{"id": "c1", "type": "function", "function": {
            "name": "finish", "arguments": "{\"status\": \"completed\", \"answer\": \"ok\"}"}}]})))
    resp = asyncio.run(llm.complete([{"role": "user", "content": "hi"}], TOOLS))
    assert resp.tool_calls[0].name == "finish" and resp.tool_calls[0].arguments["status"] == "completed"
    assert (resp.prompt_tokens, resp.completion_tokens) == (11, 7)


def test_temperature_rejection_is_retried_without_it() -> None:
    def handler(req: httpx.Request, n: int) -> httpx.Response:
        if "temperature" in json.loads(req.content):
            return httpx.Response(400, json={"error": {"message": "temperature is not supported by this model"}})
        return httpx.Response(200, json=completion({"content": '{"tool": "finish", "arguments": {"status": "x"}}'}))

    llm, bodies = llm_with(handler)
    resp = asyncio.run(llm.complete([{"role": "user", "content": "hi"}], TOOLS))
    assert len(bodies) == 2 and "temperature" not in bodies[1]
    assert resp.tool_calls[0].from_text and resp.tool_calls[0].arguments == {"status": "x"}


def test_tools_rejection_switches_to_text_protocol() -> None:
    def handler(req: httpx.Request, n: int) -> httpx.Response:
        if "tools" in json.loads(req.content):
            return httpx.Response(400, json={"error": {"message": "tools are not supported"}})
        return httpx.Response(200, json=completion({"content": '```json\n[{"tool": "finish", "arguments": {}}]\n```'}))

    llm, bodies = llm_with(handler)
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}]
    resp = asyncio.run(llm.complete(msgs, TOOLS))
    assert resp.tool_calls[0].name == "finish"
    assert "Calling tools" in bodies[-1]["messages"][0]["content"]


def test_server_errors_retry_then_give_up(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("bodhan_agent.llm.asyncio.sleep", no_sleep)
    llm, bodies = llm_with(lambda req, n: httpx.Response(503, json={"error": {"message": "busy"}}))
    with pytest.raises(LLMError):
        asyncio.run(llm.complete([{"role": "user", "content": "hi"}], TOOLS))
    assert len(bodies) == 3  # first try + max_retries


def test_rate_limits_wait_as_long_as_the_provider_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []

    async def record_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("bodhan_agent.llm.asyncio.sleep", record_sleep)

    def handler(req: httpx.Request, n: int) -> httpx.Response:
        if n == 1:
            return httpx.Response(429, headers={"retry-after": "20"}, json={"error": {"message": "slow down"}})
        if n <= 4:  # more 429s than max_retries=2 allows for other errors
            return httpx.Response(429, json={"error": {"message": "Rate limit reached. Please try again in 14.57s."}})
        return httpx.Response(200, json=completion({"content": "ok"}))

    llm, bodies = llm_with(handler)
    asyncio.run(llm.complete([{"role": "user", "content": "hi"}], TOOLS))
    assert len(bodies) == 5
    assert waits[0] >= 20 and waits[1] >= 14.5


def test_retry_after_parsing() -> None:
    from bodhan_agent.llm import retry_after_s

    assert retry_after_s(Exception("Please try again in 1m2.5s")) == pytest.approx(62.5)
    assert retry_after_s(Exception("try again in 450ms")) == pytest.approx(0.45)
    assert retry_after_s(Exception("no hint")) is None


def test_auth_error_is_not_retried() -> None:
    llm, bodies = llm_with(lambda req, n: httpx.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(LLMError):
        asyncio.run(llm.complete([{"role": "user", "content": "hi"}], TOOLS))
    assert len(bodies) == 1


def test_split_files_handles_repeats_and_commas(tmp_path: Path) -> None:
    comma_dir = tmp_path / "a,b"
    comma_dir.mkdir()
    assert split_files(["x.wav,y.wav", " z.pdf ", str(comma_dir), ""]) == ["x.wav", "y.wav", "z.pdf", str(comma_dir)]


def test_parser_defaults() -> None:
    args = build_parser().parse_args(["--task", "t", "--files", "a", "--files", "b"])
    assert args.files == ["a", "b"] and args.max_steps == 30 and args.max_cost_inr == 20.0
    assert args.transport == "inprocess" and args.out == "out"


def test_missing_llm_config_fails_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_MODEL", raising=False)
    for name in ("BODHAN_WORKDIR", "BODHAN_MAX_COST_INR"):  # main() sets these; restore afterwards
        monkeypatch.setenv(name, "unset")
    monkeypatch.chdir(tmp_path)  # keep any developer .env out of the test
    out = tmp_path / "out"
    code = main(["--task", "say hi", "--files", str(tmp_path / "missing.wav"), "--out", str(out)])
    assert code == 1
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "failed" and "LLM_MODEL" in report["answer"]
    assert (out / "work").is_dir()
