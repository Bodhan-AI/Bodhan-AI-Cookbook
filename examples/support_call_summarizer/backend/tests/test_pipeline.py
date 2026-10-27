"""Pipeline scenarios from the spec (section 13), with a fake Bodhan client."""

import httpx
import pytest
import respx

from app import bodhan_client
from app.analysis import CallAnalyzer
from app.bodhan_client import BodhanClient, Translation
from app.models import CallStatus, Sentiment, Stage
from app.pipeline import CallInput, PipelineError, process_call
from app.sarvam_client import SarvamError
from app.segmenter import SpeakerTurn
from tests.conftest import FakeBodhan, FakeSarvam, bodhan_error, needs_ffmpeg

pytestmark = needs_ffmpeg

PIECES = 4  # CONVERSATION has four utterances separated by >= 1.5 s pauses


def call_input(path, language="hi"):
    return CallInput(call_id="test1234", audio_path=path, source_file="call.wav", language=language, ticket_id="T-1")


async def run(call_wav, tmp_path, settings, client, language="hi", diarizer=None, analyzer=None):
    progress = []
    record = await process_call(
        call_input(call_wav, language),
        client=client,
        settings=settings,
        workdir=tmp_path,
        diarizer=diarizer,
        analyzer=analyzer,
        on_progress=progress.append,
    )
    return record, progress


async def test_success(call_wav, tmp_path, settings):
    fake = FakeBodhan()
    record, progress = await run(call_wav, tmp_path, settings, fake)
    assert record.status == CallStatus.completed
    assert len(record.segments) == PIECES
    assert fake.transcribe_calls == ["hi"] * PIECES
    assert [s.segment_no for s in record.segments] == [1, 2, 3, 4]
    assert all(s.english_text == f"EN({s.original_text})" for s in record.segments)
    assert record.segments[0].start < 0.6 and record.segments[-1].end > 22
    assert record.duration_seconds == pytest.approx(24.4, abs=0.1)
    assert record.ticket_id == "T-1" and record.source_language == "hi"
    assert record.processed_at.utcoffset().total_seconds() == 5.5 * 3600
    assert record.full_text_english.count("\n") == PIECES - 1
    assert record.speaker_count is None and record.segments[0].speaker is None
    stages = [p.stage for p in progress]
    assert stages[0] == Stage.converting and stages[-1] == Stage.done
    assert "Translating piece 1 of 4" in [p.message for p in progress]


async def test_one_transcription_failure_marks_call_partial(call_wav, tmp_path, settings):
    fake = FakeBodhan(transcribe=lambda n: bodhan_error(500, "internal_error") if n == 2 else f"text {n}")
    record, _ = await run(call_wav, tmp_path, settings, fake)
    assert record.status == CallStatus.partial
    failed = [s for s in record.segments if s.error]
    assert len(failed) == 1 and "internal_error" in failed[0].error
    assert failed[0].english_text is None
    assert len(fake.translate_calls) == PIECES - 1
    assert any("1 of 4 pieces" in w for w in record.warnings)


async def test_every_transcription_failing_fails_the_call(call_wav, tmp_path, settings):
    fake = FakeBodhan(transcribe=lambda n: bodhan_error(503, "internal_error"))
    with pytest.raises(PipelineError) as e:
        await run(call_wav, tmp_path, settings, fake)
    assert e.value.code == "transcription_failed"
    assert e.value.record.status == CallStatus.failed


async def test_translation_failure_marks_call_partial(call_wav, tmp_path, settings):
    fake = FakeBodhan(translate=lambda t: bodhan_error(500, "internal_error") if "2" in t else Translation(t, False))
    record, _ = await run(call_wav, tmp_path, settings, fake)
    assert record.status == CallStatus.partial
    bad = record.segments[1]
    assert bad.original_text == "नमस्ते 2" and bad.english_text is None and "Translation failed" in bad.error


async def test_truncated_translation_is_not_shown_as_complete(call_wav, tmp_path, settings):
    fake = FakeBodhan(translate=lambda t: Translation("Hello my inter", truncated="1" in t))
    record, _ = await run(call_wav, tmp_path, settings, fake)
    assert record.status == CallStatus.partial
    first = record.segments[0]
    assert first.english_text is None and "truncated" in first.error
    assert record.segments[1].english_text == "Hello my inter"


@respx.mock
async def test_rate_limit_is_retried_inside_the_pipeline(call_wav, tmp_path, settings, monkeypatch):
    async def no_sleep(_seconds):
        pass

    monkeypatch.setattr(bodhan_client.asyncio, "sleep", no_sleep)
    limited = httpx.Response(429, json={"error": {"code": "rate_limit_exceeded_error", "message": "slow down"}})
    stt = respx.post("https://api.bodhan.ai/v1/audio/transcriptions").mock(
        side_effect=[limited] + [httpx.Response(200, json={"text": "नमस्ते"})] * PIECES
    )
    respx.post("https://api.bodhan.ai/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "Hello"}}]})
    )
    client = BodhanClient("test-key-placeholder", concurrency=1)
    record, _ = await run(call_wav, tmp_path, settings, client)
    await client.aclose()
    assert record.status == CallStatus.completed
    assert stt.call_count == PIECES + 1
    assert all(s.english_text == "Hello" for s in record.segments)


async def test_empty_transcript_skips_translation(call_wav, tmp_path, settings):
    fake = FakeBodhan(transcribe=lambda n: "" if n == 3 else f"text {n}")
    record, _ = await run(call_wav, tmp_path, settings, fake)
    assert record.status == CallStatus.completed
    assert record.segments[2].original_text == "" and record.segments[2].english_text == ""
    assert len(fake.translate_calls) == PIECES - 1


async def test_english_call_skips_translation(call_wav, tmp_path, settings):
    fake = FakeBodhan(transcribe=lambda n: f"hello {n}")
    record, _ = await run(call_wav, tmp_path, settings, fake, language="en")
    assert fake.translate_calls == []
    assert all(s.english_text == s.original_text for s in record.segments)
    assert record.status == CallStatus.completed


async def test_bhojpuri_call_has_no_translation(call_wav, tmp_path, settings):
    fake = FakeBodhan()
    record, _ = await run(call_wav, tmp_path, settings, fake, language="bho")
    assert fake.translate_calls == []
    assert record.translation_available is False
    assert record.status == CallStatus.completed
    assert all(s.english_text is None and s.original_text for s in record.segments)
    assert record.full_text_english == ""
    assert any("does not support" in w for w in record.warnings)


async def test_auto_detect_sends_no_language(call_wav, tmp_path, settings):
    fake = FakeBodhan()
    record, _ = await run(call_wav, tmp_path, settings, fake, language=None)
    assert fake.transcribe_calls == [None] * PIECES
    assert record.source_language is None and len(fake.translate_calls) == PIECES


async def test_rejected_api_key_fails_the_whole_call(call_wav, tmp_path, settings):
    fake = FakeBodhan(transcribe=lambda n: bodhan_error(401, "invalid_api_key_error"))
    with pytest.raises(PipelineError) as e:
        await run(call_wav, tmp_path, settings, fake)
    assert e.value.code == "bodhan_auth"


async def test_too_long_call_is_rejected_after_conversion(call_wav, tmp_path, settings):
    settings.max_duration_seconds = 10
    with pytest.raises(Exception) as e:
        await run(call_wav, tmp_path, settings, FakeBodhan())
    assert getattr(e.value, "code", None) == "call_too_long"


class FakeDiarizer:
    def __init__(self, turns=None, fail=False):
        self.turns, self.fail = turns or [], fail

    async def diarize(self, wav_path, duration):
        if self.fail:
            raise RuntimeError("model down")
        return self.turns

    async def aclose(self):
        pass


async def test_diarization_labels_speakers(call_wav, tmp_path, settings):
    turns = [
        SpeakerTurn(0.4, 4.6, "spk_a"),
        SpeakerTurn(5.9, 12.2, "spk_b"),
        SpeakerTurn(13.5, 16.8, "spk_a"),
        SpeakerTurn(18.5, 23.7, "spk_b"),
    ]
    record, progress = await run(call_wav, tmp_path, settings, FakeBodhan(), diarizer=FakeDiarizer(turns))
    assert [s.speaker for s in record.segments] == ["Speaker 1", "Speaker 2", "Speaker 1", "Speaker 2"]
    assert record.speaker_count == 2
    assert record.full_text_english.startswith("Speaker 1: ")
    assert Stage.diarizing in [p.stage for p in progress]


async def test_diarization_failure_falls_back_to_pauses(call_wav, tmp_path, settings):
    record, _ = await run(call_wav, tmp_path, settings, FakeBodhan(), diarizer=FakeDiarizer(fail=True))
    assert record.status == CallStatus.completed
    assert len(record.segments) == PIECES and record.segments[0].speaker is None
    assert any("Speaker identification failed" in w for w in record.warnings)


async def test_without_analyzer_there_is_no_analysis(call_wav, tmp_path, settings):
    record, progress = await run(call_wav, tmp_path, settings, FakeBodhan())
    assert record.analysis is None and all(s.sentiment is None for s in record.segments)
    assert Stage.analyzing not in [p.stage for p in progress]


async def test_analysis_summarizes_the_english_transcript(call_wav, tmp_path, settings):
    sarvam = FakeSarvam()
    record, progress = await run(call_wav, tmp_path, settings, FakeBodhan(), analyzer=CallAnalyzer(sarvam))
    assert record.status == CallStatus.completed
    assert record.analysis.overall_sentiment == Sentiment.mixed
    assert [s.sentiment for s in record.segments] == ["negative", "neutral", "neutral", "positive"]
    assert "EN(नमस्ते 1)" in sarvam.calls[0][1]["content"]
    stages = [p.stage for p in progress]
    assert stages.index(Stage.analyzing) > stages.index(Stage.translating)


async def test_analysis_failure_is_only_a_warning(call_wav, tmp_path, settings):
    fake = FakeSarvam(lambda _m: SarvamError(503, "internal_server_error", "busy"))
    record, _ = await run(call_wav, tmp_path, settings, FakeBodhan(), analyzer=CallAnalyzer(fake))
    assert record.status == CallStatus.completed and record.analysis is None
    assert all(s.sentiment is None for s in record.segments)
    assert any("AI summary" in w for w in record.warnings)
