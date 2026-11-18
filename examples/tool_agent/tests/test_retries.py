from __future__ import annotations

from types import SimpleNamespace

import pytest

from bodhan_agent import bodhan
from bodhan_agent.errors import ToolError


class FakeApiStatusError(Exception):
    def __init__(self, status_code, retry_after=None):
        self.status_code = status_code
        self.message = "test response"
        self.response = SimpleNamespace(headers={"retry-after": retry_after} if retry_after else {})


def test_sdk_retry_honors_retry_after_and_stops_after_success(monkeypatch):
    monkeypatch.setattr(bodhan, "APIStatusError", FakeApiStatusError)
    delays = []
    monkeypatch.setattr(bodhan.time, "sleep", delays.append)
    calls = []

    def request():
        calls.append(1)
        if len(calls) < 3:
            raise FakeApiStatusError(429, "0.25")
        return "success"

    assert bodhan._retry_sdk(request) == "success"
    assert len(calls) == 3
    assert delays == [0.25, 0.25]


def test_sdk_retry_does_not_retry_nonretryable_status(monkeypatch):
    monkeypatch.setattr(bodhan, "APIStatusError", FakeApiStatusError)
    delays = []
    monkeypatch.setattr(bodhan.time, "sleep", delays.append)
    calls = []

    def request():
        calls.append(1)
        raise FakeApiStatusError(401)

    with pytest.raises(ToolError, match="HTTP 401") as error:
        bodhan._retry_sdk(request)
    assert error.value.code == "bodhan_api_error"
    assert len(calls) == 1
    assert delays == []
