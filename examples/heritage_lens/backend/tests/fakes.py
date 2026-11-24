"""Test doubles that mimic BodhanClient.request() without any network or httpx."""
from __future__ import annotations

from app.services.bodhan.exceptions import BodhanResponse


class FakeClient:
    def __init__(self, responder):
        self.responder = responder
        self.calls: list[dict] = []

    async def request(self, service, method, path, **kw):
        call = {"service": service, "method": method, "path": path, **kw}
        self.calls.append(call)
        result = self.responder(call)
        if isinstance(result, Exception):
            raise result
        return result


def json_response(body, status=200, request_id="req-test"):
    return BodhanResponse(status, {"content-type": "application/json"}, b"", body, request_id)


def wav_response(data: bytes, content_type="audio/wav"):
    return BodhanResponse(200, {"content-type": content_type}, data, None, "req-wav")
