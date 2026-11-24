import asyncio
import json
import wave

from bodhan_eval.cache import Cache
from bodhan_eval.clients.fake import build_fake_clients
from bodhan_eval.cost import CostLedger
from bodhan_eval.dataset import load_dataset
from bodhan_eval.logging_utils import StructuredLogger
from bodhan_eval.pipeline import RunConfig, run_pipeline
from bodhan_eval.speech import analyze_signal


def test_invalid_field_types_are_skipped(tmp_path):
    path = tmp_path / "input.jsonl"
    path.write_text('\n'.join([json.dumps({"id": "bad", "source": "text", "lang": []}),
                                json.dumps({"id": "ok", "source": "text", "lang": "hi"})]),
                    encoding="utf-8")
    report = load_dataset(path, ["hi"])
    assert [x.id for x in report.items] == ["ok"]
    assert report.skipped[0]["reason"] == "invalid_field_type"


def test_audio_only_reference_is_checked_and_failures_are_uncertain(tmp_path):
    audio = tmp_path / "audio.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x00\x00" * 240)
    assert analyze_signal(str(audio)).ok
    dataset = tmp_path / "data.jsonl"
    dataset.write_text(json.dumps({"id": "x", "source": "Hello", "lang": "hi",
                                   "reference": "नमस्ते", "audio": "audio.wav"},
                                  ensure_ascii=False), encoding="utf-8")
    cache = Cache(tmp_path / "cache")
    clients = build_fake_clients(cache, CostLedger())

    async def fail(*args):
        raise RuntimeError("ASR unavailable")

    clients.transcribe.transcribe = fail
    cfg = RunConfig(dataset=str(dataset), langs=["hi"], out=str(tmp_path), no_judge=True)
    state = asyncio.run(run_pipeline(cfg, clients, None, cache, StructuredLogger(None)))
    assert state.results["x"].status == "uncertain"
    assert "ASR unavailable" in state.results["x"].details["speech_check_error"]
