"""The real OpenAI-compatible wrapper, with the network replaced by a stub client
(the scripted LLM in other tests bypasses this class, so it needs its own tests)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
from openai import BadRequestError

from bodhan_agent.llm import OpenAICompatibleLLM
from bodhan_agent.prompts import PLAN_TOOL


def _resp(name: str, args: dict) -> SimpleNamespace:
    call = SimpleNamespace(id="c1", function=SimpleNamespace(name=name, arguments=json.dumps(args)))
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[call]))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


def _bad_request(msg: str) -> BadRequestError:
    req = httpx.Request("POST", "http://llm.test/v1/chat/completions")
    return BadRequestError(msg, response=httpx.Response(400, request=req), body=None)


class StubCompletions:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.kwargs = []

    async def create(self, **kwargs):
        self.kwargs.append(kwargs)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def _llm(outcomes) -> tuple[OpenAICompatibleLLM, StubCompletions]:
    llm = OpenAICompatibleLLM("http://llm.test/v1", "stub-model", "stub-key")
    stub = StubCompletions(outcomes)
    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=stub))
    return llm, stub


async def test_forced_tool_call_is_parsed():
    llm, stub = _llm([_resp("submit_plan", {"feasible": True})])
    reply = await llm.complete([{"role": "user", "content": "x"}], [PLAN_TOOL], force_tool="submit_plan")
    assert reply.tool_calls[0].name == "submit_plan" and reply.tool_calls[0].arguments == {"feasible": True}
    assert stub.kwargs[0]["tool_choice"] == {"type": "function", "function": {"name": "submit_plan"}}
    assert reply.usage == {"prompt_tokens": 10, "completion_tokens": 5}


async def test_provider_tool_validation_error_is_retried():
    err = _bad_request("Tool call validation failed: parameters for tool submit_plan did not match schema")
    llm, stub = _llm([err, _resp("submit_plan", {"feasible": False})])
    reply = await llm.complete([{"role": "user", "content": "x"}], [PLAN_TOOL], force_tool="submit_plan")
    assert reply.tool_calls[0].arguments == {"feasible": False} and len(stub.kwargs) == 2


async def test_unsupported_named_tool_choice_falls_back_to_auto():
    llm, stub = _llm(
        [_bad_request("tool_choice of type function is not supported"), _resp("submit_plan", {"feasible": True})]
    )
    reply = await llm.complete([{"role": "user", "content": "x"}], [PLAN_TOOL], force_tool="submit_plan")
    assert reply.tool_calls and stub.kwargs[-1]["tool_choice"] == "auto"


async def test_unparseable_generation_is_retried():
    err = _bad_request("Parsing failed. The model generated output that could not be parsed. See 'failed_generation'")
    llm, stub = _llm([err, _resp("finish", {"status": "failed"})])
    reply = await llm.complete([{"role": "user", "content": "x"}], [PLAN_TOOL])
    assert reply.tool_calls[0].name == "finish" and len(stub.kwargs) == 2
