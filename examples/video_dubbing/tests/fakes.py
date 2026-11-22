"""Shared test doubles: in-memory WAVs and a scripted HTTP session."""

import io
import json
import wave

import requests


def make_wav(seconds: float, rate: int = 16_000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


def response(status: int, body: object = None, content: bytes | None = None) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status
    resp._content = content if content is not None else json.dumps(body).encode()
    return resp


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
