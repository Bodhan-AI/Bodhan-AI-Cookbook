import io
import json
import wave
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
import soundfile as sf

from bodhan_eval.cache import Cache
from bodhan_eval.clients.bodhan import BodhanSpeak, BodhanTranscribe, _split_speech_text
from bodhan_eval.concurrency import ConcurrencyLimiter
from bodhan_eval.cost import CostLedger
from bodhan_eval.diff import compute_diff
from bodhan_eval.logging_utils import StructuredLogger
from bodhan_eval.models import Flag, ItemResult


@pytest.mark.asyncio
async def test_long_speech_and_transcription_are_chunked_and_cached(tmp_path, monkeypatch):
    calls = []
    audio = io.BytesIO()
    with wave.open(audio, "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x00\x00" * 2400)

    class HttpClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            calls.append(kwargs["json"]["input"])
            return httpx.Response(200, content=audio.getvalue(), request=httpx.Request("POST", url))

    class Transcriptions:
        async def create(self, **kwargs):
            asr_calls.append(kwargs["file"].name)
            return SimpleNamespace(text="spoken")

    asr_calls = []
    monkeypatch.setattr(httpx, "AsyncClient", HttpClient)
    monkeypatch.setattr("bodhan_eval.clients.bodhan._openai_client", lambda *_: SimpleNamespace(
        audio=SimpleNamespace(transcriptions=Transcriptions())))
    cache, ledger = Cache(tmp_path / "cache"), CostLedger()
    limiter, logger = ConcurrencyLimiter(2), StructuredLogger(None)
    speak = BodhanSpeak({"model": "indic-speak", "endpoint": "/audio/speech",
                         "rate_inr_per_10k_input_chars": 6}, "https://example/v1", "key",
                        cache, ledger, limiter, logger)
    text = "A sentence. " * 50
    assert "".join(_split_speech_text(text)) == text
    result = await speak.speak(text, "hi")
    assert len(calls) > 1 and sf.info(result.audio_path).duration > 0.1
    count = len(calls)
    assert (await speak.speak(text, "hi")).cached
    assert len(calls) == count
    path = tmp_path / "long.wav"
    sf.write(path, np.concatenate([np.full(8000, value, dtype="float32")
                                   for value in (0.0, 0.1, 0.2, 0.3)]), 8000)
    transcribe = BodhanTranscribe({"model": "indic-transcribe", "endpoint": "/audio/transcriptions",
                                  "rate_inr_per_audio_minute": .1, "max_audio_seconds": 1},
                                 "https://example/v1", "key", cache, ledger, limiter, logger)
    result = await transcribe.transcribe(str(path), "hi")
    assert result.text == "spoken spoken spoken spoken" and len(asr_calls) == 4
    assert (await transcribe.transcribe(str(path), "hi")).cached
    assert len(asr_calls) == 4


def test_diff_does_not_treat_skipped_as_improvement_or_severity_change_as_new(tmp_path):
    baseline = [
        {"id": "x", "status": "flagged", "lang": "hi",
         "flags": [{"category": "format", "severity": "major"}]},
        {"id": "y", "status": "flagged", "lang": "hi",
         "flags": [{"category": "omission", "severity": "major"}]},
    ]
    (tmp_path / "items.jsonl").write_text("\n".join(map(json.dumps, baseline)), encoding="utf-8")
    current = [ItemResult("x", "hi", status="flagged", flags=[Flag("format", "minor", 1)]),
               ItemResult("y", "hi", status="skipped")]
    diff = compute_diff(str(tmp_path), current)
    assert diff["regression_count"] == diff["improvement_count"] == 0
    assert diff["severity_changes"][0]["to"] == "minor"
    assert diff["incomparable_items"] == ["y"]
