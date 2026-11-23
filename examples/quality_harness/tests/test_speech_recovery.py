import asyncio
import json

import numpy as np
import soundfile as sf

from bodhan_eval.cache import Cache
from bodhan_eval.clients.base import TranscribeResult
from bodhan_eval.clients.fake import build_fake_clients
from bodhan_eval.cost import CostLedger
from bodhan_eval.logging_utils import StructuredLogger
from bodhan_eval.pipeline import RunConfig, run_pipeline


def test_degenerate_transcription_is_retried_on_segments(tmp_path):
    sf.write(tmp_path / "input.wav", np.ones(4000, dtype="float32") * 0.1, 1000)
    dataset = tmp_path / "input.jsonl"
    dataset.write_text(json.dumps({"id": "x", "source": "Hello world", "lang": "hi",
                                   "translation": "नमस्ते दुनिया", "audio": "input.wav"},
                                  ensure_ascii=False), encoding="utf-8")
    cache = Cache(tmp_path / "cache")
    clients = build_fake_clients(cache, CostLedger())
    calls = []

    async def transcribe(path, lang):
        calls.append((path, lang))
        text = "\u0b44" * 186 if len(calls) == 1 else ("नमस्ते" if len(calls) == 2 else "दुनिया")
        return TranscribeResult(text=text, audio_seconds=2, cost_inr=0, cached=False)

    clients.transcribe.transcribe = transcribe
    cfg = RunConfig(dataset=str(dataset), langs=["hi"], out=str(tmp_path), no_judge=True)
    state = asyncio.run(run_pipeline(cfg, clients, None, cache, StructuredLogger(None)))
    result = state.results["x"]
    assert len(calls) == 3
    assert result.details["speech_checker_recovered"] is True
    assert "speech_check_error" not in result.details
    assert "s6_omission" in result.checks_run
