"""Shared fixtures: a fake Bodhan API served through httpx.MockTransport, so tests exercise the real client
(retries, cache, cost ledger) without network access or API keys."""
import io
import json

import httpx
import numpy as np
import pytest
import soundfile as sf

from bodhan_mcp import config, textutil as tx
from bodhan_mcp.client import BodhanClient, set_client


class FakeBodhan:
    def __init__(self):
        self.requests: list[tuple[str, dict]] = []
        self.fail_next: list[int] = []        # HTTP statuses to return before succeeding
        self.stt_text = "hello"               # what Indic-Transcribe "hears"
        self.truncate_over: int | None = None  # translate reports truncated for inputs longer than this
        self.ocr_blocks: list[dict] = []
        self.ocr_markdown = "page text"
        self.tts_scale = 1.0                  # <1 simulates omission (audio too short)

    @staticmethod
    def _chat(content, finish, tokens):
        return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": finish}],
                                         "usage": {"completion_tokens": tokens}})

    def count(self, kind: str) -> int:
        return sum(1 for k, _ in self.requests if k == kind)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if self.fail_next:
            code = self.fail_next.pop(0)
            return httpx.Response(code, json={"error": {"message": "boom", "code": str(code)}})
        if path.endswith("/audio/transcriptions"):
            raw = req.content
            wav = raw[raw.find(b"RIFF"):]
            x, sr = sf.read(io.BytesIO(wav))
            self.requests.append(("stt", {"dur": len(x) / sr}))
            return httpx.Response(200, json={"text": self.stt_text})
        if path.endswith("/chat/completions") and json.loads(req.content).get("model") == "indic-translate":
            body = json.loads(req.content)
            text = body["messages"][0]["content"]
            self.requests.append(("mt", {"text": text, **body}))
            if self.truncate_over and len(text) > self.truncate_over:
                return self._chat("தமிழ்", "length", 5)
            out = []
            for para in text.split("\n\n"):
                if para.lstrip().startswith(("|", "$$")):
                    out.append(para)
                    continue
                sents = tx.split_sentences(para) or [para]
                out.append(" ".join(" ".join(["தமிழ்"] * len(s.split())) + "." + "".join(f" {n}" for n in tx.extract_numbers(s))
                                    for s in sents))
            tr = "\n\n".join(out)
            return self._chat(tr, "stop", max(len(tr) // 3, 1))
        if path.endswith("/audio/speech"):
            body = json.loads(req.content)
            self.requests.append(("tts", body))
            dur = len(body["input"]) / 14 * self.tts_scale
            t = np.linspace(0, dur, int(24000 * dur), endpoint=False)
            buf = io.BytesIO()
            sf.write(buf, (0.3 * np.sin(2 * np.pi * 220 * t)).astype("float32"), 24000, format="WAV", subtype="PCM_16")
            return httpx.Response(200, content=buf.getvalue(), headers={"content-type": "audio/wav"})
        if path.endswith("/chat/completions"):
            self.requests.append(("ocr", {}))
            return httpx.Response(200, json={"choices": [{"message": {"content": self.ocr_markdown}}],
                                             "blocks": self.ocr_blocks, "usage": {}})
        return httpx.Response(404, json={"error": {"message": "no route"}})


@pytest.fixture
def fake(tmp_path, monkeypatch):
    for kind, env in config.KEY_ENV.items():
        monkeypatch.setenv(env, "test-key")
    monkeypatch.setenv("BODHAN_WORKDIR", str(tmp_path))
    monkeypatch.setenv("BODHAN_STRICT_PROVENANCE", "1")
    monkeypatch.delenv("BODHAN_EXTRA_ROOTS", raising=False)
    f = FakeBodhan()
    set_client(BodhanClient(cache_dir=tmp_path / "cache", transport=httpx.MockTransport(f), backoff_base=0.0))
    yield f
    set_client(None)


def make_wav(path, seconds, sr=16000, speech_pattern=None):
    """Tone bursts separated by short gaps, so the chunker has pauses to cut at."""
    t = np.arange(int(seconds * sr)) / sr
    x = 0.3 * np.sin(2 * np.pi * 200 * t)
    period = 5.0
    gap = (t % period) > (period - 0.5)
    x[gap] = 0
    sf.write(str(path), x.astype("float32"), sr, subtype="PCM_16")
    return path
