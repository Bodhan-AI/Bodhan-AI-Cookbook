"""Live failure regressions, simulated locally without external credentials."""
from __future__ import annotations

import os
import requests

from bodhan_agent.runner import OpenAICompatibleBrain
from bodhan_shared.gateway import Gateway
from bodhan_shared.store import Workspace
from bodhan_shared.errors import TaskError


def _response(status, json_value=None, headers=None):
    r = requests.Response()
    r.status_code = status
    r._content = __import__("json").dumps(json_value or {}).encode()
    r.headers.update(headers or {})
    return r


class QueueSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_wrong_bodhan_v1_root_is_normalized(monkeypatch, tmp_path):
    monkeypatch.setenv("BODHAN_BASE_URL", "https://api.bodhan.ai/v1/")
    gateway = Gateway(Workspace(tmp_path))
    assert gateway.base_url == "https://api.bodhan.ai"


def test_llm_429_is_retried_with_same_tools(monkeypatch):
    import bodhan_agent.runner as runner
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "mock-key")
    session = QueueSession([
        _response(429, headers={"Retry-After": "0.1"}),
        _response(200, {"choices": [{"message": {"role": "assistant", "content": "done"}}]}),
    ])
    brain = OpenAICompatibleBrain(session)
    actual = brain.complete([{"role": "user", "content": "hi"}])
    assert actual["content"] == "done"
    assert len(session.calls) == 2
    assert session.calls[0][1]["json"] == session.calls[1][1]["json"]


def test_llm_persistent_429_reports_limit(monkeypatch):
    import bodhan_agent.runner as runner
    monkeypatch.setattr(runner.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "mock-key")
    session = QueueSession([_response(429, headers={"Retry-After": "0.1"}) for _ in range(3)])
    try:
        OpenAICompatibleBrain(session).complete([{"role": "user", "content": "hi"}])
    except TaskError as exc:
        assert exc.code == "LLM_RATE_LIMIT"
    else:
        raise AssertionError("Expected LLM_RATE_LIMIT")
    assert len(session.calls) == 3
