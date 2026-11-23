import wave
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from bodhan_eval.budget import Budget
from bodhan_eval.client.errors import BodhanAPIError
from bodhan_eval.models import Item
from bodhan_eval.speech_stage import run_speech_stage


class FakeSpeechClient:
    def __init__(
        self,
        *,
        transcript: str = "",
        speak_audio: bytes = b"",
        error: Exception | None = None,
    ) -> None:
        self.transcript = transcript
        self.speak_audio = speak_audio
        self.error = error
        self.speak_calls: list[tuple[str, str, str]] = []
        self.transcribe_calls: list[tuple[bytes, str, float | None]] = []

    async def speak(self, text: str, voice: str, lang: str) -> bytes:
        self.speak_calls.append((text, voice, lang))
        if self.error:
            raise self.error
        return self.speak_audio

    async def transcribe(
        self, wav_bytes: bytes, language: str, *, duration_s: float | None = None
    ) -> str:
        self.transcribe_calls.append((wav_bytes, language, duration_s))
        if self.error:
            raise self.error
        return self.transcript


def _wav_bytes(duration_s: float = 1.0, active: bool = True) -> bytes:
    samples = np.zeros(int(24_000 * duration_s), dtype="<i2")
    if active:
        samples[: min(len(samples), 12_000)] = 5000
    from io import BytesIO

    buffer = BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(24_000)
        stream.writeframes(samples.tobytes())
    return buffer.getvalue()


def _records(items: list[Item]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "lang": item.lang,
            "terminal_state": "scored",
            "checks_run": [],
            "flags": [],
            "decisions": [],
            "failed_checks": [],
            "skipped_reason": None,
        }
        for item in items
    ]


@pytest.mark.asyncio
async def test_supplied_audio_runs_asr_and_acoustic_checks(tmp_path: Path) -> None:
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    audio_path = audio_dir / "silent.wav"
    audio_path.write_bytes(_wav_bytes(active=False))
    item = Item(
        "silence",
        "hi",
        "The train arrives.",
        "ट्रेन आती है।",
        audio=audio_path,
    )
    client = FakeSpeechClient(transcript="यह एक लंबा और सहज वाक्य है जिसमें बहुत शब्द हैं।")
    records = _records([item])

    await run_speech_stage(
        [item],
        records,
        client,
        Budget(1.0),
        enabled=True,
        generate=False,
        speech_sample=0,
    )

    assert len(client.transcribe_calls) == 1
    assert client.transcribe_calls[0][1] == "hi"
    assert records[0]["speech"]["status"] == "complete"
    assert any(
        flag["category"] == "garbled"
        and flag["evidence"]["suspected_asr_hallucination"]
        for flag in records[0]["flags"]
    )


@pytest.mark.asyncio
async def test_generated_speech_is_stratified_and_transcribed(tmp_path: Path) -> None:
    items = [
        Item("ta-1", "ta", "Source", "தமிழ்"),
        Item("hi-1", "hi", "Source", "हिन्दी"),
        Item("hi-2", "hi", "Source", "दूसरा"),
        Item("bn-1", "bn", "Source", "বাংলা"),
    ]
    client = FakeSpeechClient(
        transcript="A short transcript",
        speak_audio=_wav_bytes(),
    )
    records = _records(items)

    await run_speech_stage(
        items,
        records,
        client,
        Budget(1.0),
        enabled=True,
        generate=True,
        speech_sample=2,
    )

    assert [call[2] for call in client.speak_calls] == ["bn", "hi"]
    assert len(client.transcribe_calls) == 2
    sampled = [record for record in records if record.get("speech", {}).get("sampled")]
    assert len(sampled) == 2
    assert all(record["speech"]["status"] == "complete" for record in sampled)


@pytest.mark.asyncio
async def test_disabled_speech_does_not_call_client() -> None:
    item = Item("no-audio", "hi", "Source", "अनुवाद")
    client = FakeSpeechClient(speak_audio=_wav_bytes())
    records = _records([item])

    await run_speech_stage(
        [item],
        records,
        client,
        Budget(1.0),
        enabled=False,
        generate=True,
        speech_sample=10,
    )

    assert client.speak_calls == []
    assert client.transcribe_calls == []
    assert records[0]["speech"]["status"] == "disabled"


@pytest.mark.asyncio
async def test_speech_api_failure_marks_unchecked_and_preserves_flags(tmp_path: Path) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(_wav_bytes(active=False))
    item = Item("failure", "hi", "Source", "अनुवाद", audio=audio_path)
    original = {
        "category": "number",
        "severity": "major",
        "modality": "text",
        "span": [0, 1],
        "source_span": [0, 1],
        "audio_span_s": None,
        "evidence": {},
        "detectors": ["text_numbers"],
        "confidence": 0.9,
        "suppressed_reason": None,
    }
    client = FakeSpeechClient(error=BodhanAPIError(415, "unsupported audio"))
    records = _records([item])
    records[0]["flags"] = [original]

    await run_speech_stage(
        [item],
        records,
        client,
        Budget(1.0),
        enabled=True,
        generate=False,
        speech_sample=0,
    )

    assert records[0]["terminal_state"] == "failed_checks"
    assert "speech:transcribe_api_error:415" in records[0]["failed_checks"]
    assert records[0]["flags"][0] == original
    assert records[0]["speech"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_speak_api_failure_is_distinguished_from_transcription() -> None:
    item = Item("speak-failure", "hi", "Source", "अनुवाद")
    client = FakeSpeechClient(
        speak_audio=_wav_bytes(),
        error=BodhanAPIError(401, "unauthorized"),
    )
    records = _records([item])

    await run_speech_stage(
        [item],
        records,
        client,
        Budget(1.0),
        enabled=True,
        generate=True,
        speech_sample=1,
    )

    assert records[0]["terminal_state"] == "failed_checks"
    assert "speech:speak_api_error:401" in records[0]["failed_checks"]
    assert records[0]["speech"]["reason"] == "speak_api_error:401"
    assert client.transcribe_calls == []


@pytest.mark.asyncio
async def test_generated_speech_budget_is_enforced_before_tts() -> None:
    item = Item("cost", "hi", "Source", "अनुवाद")
    client = FakeSpeechClient(speak_audio=_wav_bytes())
    records = _records([item])
    budget = Budget(0.00001)

    await run_speech_stage(
        [item],
        records,
        client,
        budget,
        enabled=True,
        generate=True,
        speech_sample=1,
    )

    assert client.speak_calls == []
    assert records[0]["speech"]["status"] == "skipped"
    assert budget.skips
