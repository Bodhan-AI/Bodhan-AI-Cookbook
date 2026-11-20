"""Offline fakes: real client code runs against httpx.MockTransport, so tests make no network calls and cost nothing."""

import array
import io
import json
import math
import types
import wave
from pathlib import Path

import httpx
import pytest

from bodhan_eval.cache import Cache
from bodhan_eval.clients import LLM, Bodhan, Budget, RunLog

DATA = Path(__file__).parent / "data"


def fixture_rows() -> dict:
    """The 10 fixture rows by id. Their translations are real Indic-Translate outputs."""
    rows = [json.loads(line) for line in (DATA / "ten.jsonl").read_text(encoding="utf-8").splitlines()]
    return {r["id"]: r for r in rows}


def item(source, translation, lang="hi", **kw):
    return {"id": "x", "lang": lang, "source": source, "translation": translation, "reference": None, **kw}


def make_wav(seconds=1.0, freq=220.0, rate=24000, amp=0.3) -> bytes:
    n = int(seconds * rate)
    samples = array.array("h", (int(amp * 32767 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


class FakeBodhan:
    """Scripted Bodhan API. `queue` holds (status, body, headers) responses served before the default behaviour."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.queue: list[tuple] = []
        # Known fixture pairs translate both ways like the real model did; anything else is echoed with a tag.
        names = {"hi": "Hindi", "ta": "Tamil", "bn": "Bengali"}
        known = {}
        for r in fixture_rows().values():
            if r.get("translation"):
                known[(r["source"], names[r["lang"]])] = r["translation"]
                known[(r["translation"], "English")] = r["source"]
        self.translate = lambda text, lang_name: known.get((text, lang_name), f"[{lang_name}] {text}")
        self.transcript = "transcript"
        self.speech = make_wav(0.5)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.queue:
            status, body, headers = self.queue.pop(0)
            content = body if isinstance(body, bytes) else json.dumps(body).encode()
            return httpx.Response(status, content=content, headers=headers or {})
        path = request.url.path
        if path == "/v1/chat/completions":
            body = json.loads(request.content)
            prompt = body["messages"][0]["content"]
            head, text = prompt.split(":\n\n", 1)
            out = self.translate(text, head.removeprefix("Translate the following text into "))
            return httpx.Response(200, json={"choices": [{"message": {"content": out}, "finish_reason": "stop"}],
                                             "usage": {"prompt_tokens": 10, "completion_tokens": len(out)}, "system_fingerprint": "fake"})
        if path == "/v1/audio/speech":
            return httpx.Response(200, content=self.speech, headers={"content-type": "audio/mpeg"})
        if path == "/v1/audio/transcriptions":
            return httpx.Response(200, json={"text": self.transcript, "usage": None})
        return httpx.Response(404, json={"detail": "Not Found"})


class FakeLLM:
    def __init__(self, reply="{}"):
        self.reply = reply
        self.requests: list[dict] = []
        self.reject: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        for p in self.reject:
            if p in body:
                return httpx.Response(400, json={"error": {"message": f"Unsupported parameter: '{p}'", "type": "invalid_request_error"}})
        return httpx.Response(200, json={"id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                                         "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant",
                                                      "content": self.reply(body) if callable(self.reply) else self.reply}}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


@pytest.fixture
def fake_bodhan():
    return FakeBodhan()


@pytest.fixture
def fake_llm():
    return FakeLLM()


ALL_KEYS = {"translate": "k1", "speak": "k2", "transcribe": "k3"}


def make_clients(tmp_path, fake_bodhan, fake_llm, keys=ALL_KEYS, budget=None, llm=True):
    log, cache = RunLog(), Cache(tmp_path / "cache")
    bodhan = Bodhan(dict(keys), cache, Budget(budget), log, transport=httpx.MockTransport(fake_bodhan), sleep=lambda s: None)
    llm_client = LLM("https://llm.example/v1/" if llm else None, "x", "fake-model" if llm else None, cache, log,
                     http_client=httpx.Client(transport=httpx.MockTransport(fake_llm)))
    return bodhan, llm_client


def make_args(tmp_path, dataset=DATA / "ten.jsonl", **kw):
    base = {"dataset": str(dataset), "langs": "hi,ta,bn", "out": str(tmp_path / "report"), "generate": False, "baseline": None,
            "calibration": None, "max_cost_inr": None, "cache_dir": str(tmp_path / "cache"), "concurrency": 2, "env_file": None}
    base.update(kw)
    return types.SimpleNamespace(**base)
