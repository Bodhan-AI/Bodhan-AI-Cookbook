import numpy as np
import pytest

from bodhan_eval.audio import WavData
from bodhan_eval.checks.speech import check_speech, check_speech_with_transcriber
from bodhan_eval.client.errors import BodhanAPIError
from bodhan_eval.models import Item
from bodhan_eval.checks.speech import mask_numbers


def test_speech_number_masking():
    assert mask_numbers("Call 123 and 456.") == "Call <NUM> and <NUM>."


class ScriptedTranscriber:
    def __init__(self, response: str | Exception):
        self.response = response
        self.calls = 0

    async def transcribe(self, wav_bytes: bytes, language: str) -> str:
        self.calls += 1
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _speech_item() -> Item:
    return Item(id="speech", lang="hi", source="The train arrives.", translation="ट्रेन आती है।")


@pytest.mark.asyncio
async def test_silence_with_fluent_asr_is_marked_as_suspected_hallucination():
    item = _speech_item()
    wav = WavData(np.zeros(24_000 * 2, dtype=np.float32), 24_000)
    transcriber = ScriptedTranscriber("यह एक लंबा और सहज वाक्य है जिसमें बहुत शब्द हैं।")

    flags = await check_speech_with_transcriber(item, wav, b"wav", transcriber)

    assert transcriber.calls == 1
    silence_flag = next(flag for flag in flags if flag.category == "garbled")
    assert silence_flag.evidence["suspected_asr_hallucination"] is True
    assert silence_flag.detectors == ["silence"]


@pytest.mark.asyncio
async def test_empty_and_wrong_script_transcripts_do_not_crash():
    item = _speech_item()
    wav = WavData(np.full(24_000, 0.1, dtype=np.float32), 24_000)
    empty_flags = await check_speech_with_transcriber(
        item, wav, b"wav", ScriptedTranscriber("")
    )
    wrong_script_flags = await check_speech_with_transcriber(
        item, wav, b"wav", ScriptedTranscriber("This is an unrelated English sentence.")
    )
    assert empty_flags == []
    assert all(flag.category != "speech_omission" for flag in wrong_script_flags)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [415, 429])
async def test_transcription_errors_are_not_hidden(status):
    item = _speech_item()
    wav = WavData(np.full(24_000, 0.1, dtype=np.float32), 24_000)
    transcriber = ScriptedTranscriber(BodhanAPIError(status, "mock transcription error"))
    with pytest.raises(BodhanAPIError) as error:
        await check_speech_with_transcriber(item, wav, b"wav", transcriber)
    assert error.value.status == status


def test_short_audio_does_not_turn_repeated_asr_into_tts_repetition():
    item = _speech_item()
    wav = WavData(np.full(24_000, 0.1, dtype=np.float32), 24_000)

    flags = check_speech(
        item,
        wav,
        transcriber=None,
        stats=None,
        transcript="namaste duniya " * 30,
    )

    assert not any(flag.category == "repetition" for flag in flags)
