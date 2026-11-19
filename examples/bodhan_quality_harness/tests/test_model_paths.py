"""Tests for the model-dependent paths, without touching the real API.

A local HTTP server mimics the three Bodhan endpoints and the judge endpoint, so
the client plumbing, retries, cost accounting, caching, budget ceiling, the
--generate stage and the --baseline diff are all exercised offline.
"""

from __future__ import annotations

import io
import json
import os
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Ensure proxy does not intercept local fake server in sandboxed environments
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["no_proxy"] = "127.0.0.1,localhost"

import numpy as np
import pytest

from bodhan_eval import config
from bodhan_eval.checks import speech as speech_checks
from bodhan_eval.cli import main

CALLS: dict[str, int] = {}
LAST_TRANSCRIBE_BODY: list[bytes] = []


def _wav(seconds: float = 1.0, freq: float = 440.0, amplitude: float = 0.3,
         silence: bool = False) -> bytes:
    sr = 16000
    n = int(sr * seconds)
    if silence:
        samples = np.zeros(n, dtype=np.int16)
    else:
        t = np.arange(n) / sr
        samples = (np.sin(2 * np.pi * freq * t) * amplitude * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


class FakeAPI(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence the test output
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        CALLS[self.path] = CALLS.get(self.path, 0) + 1

        if self.path.endswith("/chat/completions"):
            body = json.loads(raw or b"{}")
            model = body.get("model", "")
            if model == "judge-model":
                # One mistranslation flag with a span.
                payload = {"flags": [{"category": "mistranslation", "severity": "major",
                                      "span": [0, 5],
                                      "explanation": "wrong meaning in the opening phrase"}]}
                content = json.dumps(payload)
            else:
                content = "अनुवादित पाठ यहाँ है।"
            out = {"choices": [{"message": {"content": content}}]}
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if self.path.endswith("/audio/speech"):
            data = _wav(seconds=1.0)
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if self.path.endswith("/audio/transcriptions"):
            # keep the raw multipart body so tests can assert the form fields
            LAST_TRANSCRIBE_BODY.clear()
            LAST_TRANSCRIBE_BODY.append(raw)
            data = json.dumps({"text": "अनुवादित पाठ यहाँ है।"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_response(404)
        self.end_headers()


@pytest.fixture(scope="module")
def fake_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAPI)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()


@pytest.fixture
def wired(monkeypatch, fake_api):
    """Point config at the fake server and enable every model path."""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setattr(config, "BODHAN_BASE_URL", fake_api, raising=False)
    monkeypatch.setattr(config, "BODHAN_TRANSLATE_KEY", "test-key", raising=False)
    monkeypatch.setattr(config, "BODHAN_SPEAK_KEY", "test-key", raising=False)
    monkeypatch.setattr(config, "BODHAN_TRANSCRIBE_KEY", "test-key", raising=False)
    monkeypatch.setattr(config, "LLM_BASE_URL", fake_api, raising=False)
    monkeypatch.setattr(config, "LLM_MODEL", "judge-model", raising=False)
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(config, "have_translate", lambda: True, raising=False)
    monkeypatch.setattr(config, "have_speak", lambda: True, raising=False)
    monkeypatch.setattr(config, "have_transcribe", lambda: True, raising=False)
    monkeypatch.setattr(config, "have_judge", lambda: True, raising=False)
    CALLS.clear()
    return fake_api


def _dataset(tmp_path, n=3, with_translation=False):
    rows = []
    for i in range(n):
        row = {"id": f"m-{i}", "source": f"Source sentence number {i} with 42 units.",
               "lang": "hi"}
        if with_translation:
            row["translation"] = "यह एक परीक्षण वाक्य है जो संख्या 42 दर्शाता है।"
        rows.append(row)
    p = tmp_path / "d.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                 encoding="utf-8")
    return p


# --- speech signal analysis --------------------------------------------------
def test_analyse_wav_reads_a_normal_tone():
    stats = speech_checks.analyse_wav(_wav(1.0, 440.0, 0.3))
    assert stats is not None
    assert 0.9 <= stats.duration_s <= 1.1
    assert stats.silence_fraction < 0.2
    assert stats.clipping_fraction == 0.0


def test_silent_audio_is_detected():
    stats = speech_checks.analyse_wav(_wav(1.0, silence=True))
    assert stats.silence_fraction > 0.9


def test_clipped_audio_is_detected():
    stats = speech_checks.analyse_wav(_wav(0.5, 440.0, 1.0))
    assert stats.clipping_fraction > 0.01


def test_very_short_audio_flags_omission(tmp_path):
    from bodhan_eval.checks.base import ItemContext
    f = tmp_path / "short.wav"
    f.write_bytes(_wav(0.1))
    ctx = ItemContext(id="x", source="Some source text.", lang="hi", audio=str(f))
    flags = speech_checks.check_audio_signal(ctx, config.Thresholds())
    assert any(fl.category == "speech_omission" for fl in flags)


def test_repetition_in_transcript_is_flagged():
    from bodhan_eval.checks.base import ItemContext
    ctx = ItemContext(id="x", source="Some source.", lang="hi")
    flags = speech_checks.check_transcript(
        ctx, "यह वाक्य है यह वाक्य है और आगे बढ़ता है", "यह वाक्य है और आगे बढ़ता है",
        config.Thresholds())
    assert any(f.category == "speech_repetition" for f in flags)


# --- model stages ------------------------------------------------------------
def test_generate_creates_translations_and_audio(tmp_path, monkeypatch, wired):
    monkeypatch.chdir(tmp_path)
    ds = _dataset(tmp_path, n=3, with_translation=False)
    out = tmp_path / "rep"
    assert main(["--dataset", str(ds), "--langs", "hi", "--out", str(out),
                 "--generate", "--speech-sample", "2"]) == 0

    items = [json.loads(l) for l in (out / "items.jsonl").read_text().splitlines() if l]
    assert len(items) == 3
    assert any("generate.translate" in i["checks_run"] for i in items)
    assert any("generate.speak" in i["checks_run"] for i in items)
    assert any("speech.roundtrip" in i["checks_run"] for i in items)

    summary = json.loads((out / "summary.json").read_text())
    assert summary["cost"]["total_inr"] > 0
    assert summary["cost"]["calls"], "model calls must be counted"
    assert (out / "audio").exists()


def test_judge_adds_flags(tmp_path, monkeypatch, wired):
    monkeypatch.chdir(tmp_path)
    ds = _dataset(tmp_path, n=2, with_translation=True)
    out = tmp_path / "rep"
    # --judge-sample forces clean items through the judge too
    assert main(["--dataset", str(ds), "--langs", "hi", "--out", str(out),
                 "--judge-sample", "2"]) == 0
    items = [json.loads(l) for l in (out / "items.jsonl").read_text().splitlines() if l]
    judged = [i for i in items if "judge.llm" in i["checks_run"]]
    assert judged, "judge must have been called"
    assert any(f["category"] == "mistranslation" for i in judged for f in i["flags"])


def test_transcribe_sends_model_and_language(tmp_path, monkeypatch, wired):
    """Regression: the multipart form must carry model + language.

    The first version built that dict and never passed it, so the request went
    out with no model name — invisible because the fake server ignored the body.
    """
    monkeypatch.chdir(tmp_path)
    ds = _dataset(tmp_path, n=1, with_translation=False)
    out = tmp_path / "rep"
    assert main(["--dataset", str(ds), "--langs", "hi", "--out", str(out),
                 "--generate", "--speech-sample", "1"]) == 0

    assert LAST_TRANSCRIBE_BODY, "transcribe endpoint was never called"
    body = LAST_TRANSCRIBE_BODY[-1]
    assert b'name="model"' in body, "model field missing from the multipart body"
    assert config.TRANSCRIBE_MODEL.encode() in body
    assert b'name="language"' in body
    assert b"hi" in body


def test_cache_makes_rerun_free_and_identical(tmp_path, monkeypatch, wired):
    monkeypatch.chdir(tmp_path)
    ds = _dataset(tmp_path, n=2, with_translation=False)
    out1, out2 = tmp_path / "r1", tmp_path / "r2"

    assert main(["--dataset", str(ds), "--langs", "hi", "--out", str(out1),
                 "--generate", "--speech-sample", "2"]) == 0
    first_cost = json.loads((out1 / "summary.json").read_text())["cost"]["total_inr"]

    assert main(["--dataset", str(ds), "--langs", "hi", "--out", str(out2),
                 "--generate", "--speech-sample", "2"]) == 0
    second = json.loads((out2 / "summary.json").read_text())

    assert second["cost"]["total_inr"] == 0, "a cached rerun must cost nothing"
    assert first_cost > 0
    assert (out1 / "items.jsonl").read_bytes() == (out2 / "items.jsonl").read_bytes()


def test_budget_ceiling_stops_the_run(tmp_path, monkeypatch, wired):
    monkeypatch.chdir(tmp_path)
    ds = _dataset(tmp_path, n=5, with_translation=False)
    out = tmp_path / "rep"
    # A ceiling far below the cost of five translations.
    assert main(["--dataset", str(ds), "--langs", "hi", "--out", str(out),
                 "--generate", "--max-cost-inr", "0.0001"]) == 0
    summary = json.loads((out / "summary.json").read_text())
    assert summary["cost"]["total_inr"] <= 0.0001
    assert summary["items_scored"] == 5, "budget exhaustion must not abort the run"


def test_baseline_diff_is_written(tmp_path, monkeypatch, wired):
    monkeypatch.chdir(tmp_path)
    ds = _dataset(tmp_path, n=2, with_translation=True)
    base = tmp_path / "base"
    cur = tmp_path / "cur"

    assert main(["--dataset", str(ds), "--langs", "hi", "--out", str(base)]) == 0
    assert main(["--dataset", str(ds), "--langs", "hi", "--out", str(cur),
                 "--baseline", str(base)]) == 0

    diff = cur / "diff.md"
    assert diff.exists()
    text = diff.read_text()
    assert "Diff against baseline" in text
    assert "Headline change" in text
