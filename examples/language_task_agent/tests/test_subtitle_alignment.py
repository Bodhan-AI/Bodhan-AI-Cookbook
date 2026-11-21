"""Synthetic timing anchors, not recorded speech or live-model evidence."""
import wave
import hashlib
from decimal import Decimal

import httpx

import pytest

from bodhan_core import media
from bodhan_core.runtime import ToolError
from bodhan_core.gateway import Gateway


def test_late_utterance_is_not_spread_across_earlier_audio():
    text = "We are late. Come along. The wrong way to wash hands."
    anchors = [
        {"start": 0, "end": 5, "text": "We are late"},
        {"start": 5, "end": 10, "text": "Come along"},
        {"start": 10, "end": 15, "text": "The wrong way to wash hands"},
    ]
    aligned = media.align_text_windows(text, anchors)
    assert " ".join(row["text"] for row in aligned) == text
    assert all("wrong" not in row["text"] for row in aligned if row["start"] < 10)
    assert aligned[-1]["start"] == 10
    assert aligned[-1]["text"] == "The wrong way to wash hands."


def test_alignment_preserves_reference_words_when_anchors_have_errors():
    text = "Please bring the clean garden tools. Then wash your hands."
    anchors = [
        {"start": 0, "end": 5, "text": "Please bring clean gardening tools"},
        {"start": 5, "end": 10, "text": "Then wash hands"},
    ]
    aligned = media.align_text_windows(text, anchors)
    assert " ".join(row["text"] for row in aligned) == text
    assert aligned[1]["text"] == "Then wash your hands."


def test_short_sentence_start_stays_with_following_anchored_phrase():
    text = ("Oh God, there's no peace here either. It's getting late. "
            "I have to go to school, right? No, Mom, let's go to school. "
            "That's not an excuse. Let's go. The wrong way to wash hands.")
    anchors = [
        {"start": 0, "end": 4.94, "text": "Oh God, there's no peace here either. It's getting late."},
        {"start": 4.94, "end": 9.88,
         "text": "We have to go to school, right? No, Mom, let's go to school. Let's not make excuses."},
        {"start": 9.88, "end": 14.82, "text": "Wrong way to wash hands."},
    ]
    aligned = media.align_text_windows(text, anchors)
    assert " ".join(row["text"] for row in aligned) == text
    assert aligned[-1] == {"start": 9.88, "end": 14.82, "text": "The wrong way to wash hands."}


def test_unrelated_anchors_do_not_claim_timing_support():
    with pytest.raises(ToolError, match="anchor"):
        media.align_text_windows(
            "The garden needs water.",
            [{"start": 0, "end": 5, "text": "an entirely unrelated sentence"}],
        )


def test_untrusted_alignment_text_has_a_bounded_work_limit():
    with pytest.raises(ToolError, match="anchor"):
        media.align_text_windows(
            "word " * 1000,
            [{"start": 0, "end": 5, "text": "word " * 1000}],
        )


def test_balanced_timing_windows_keep_samples_and_avoid_tiny_tail(tmp_path):
    source = tmp_path / "source.wav"
    pcm = bytes.fromhex("401fc0e0") * (16000 * 7 + 800)
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(pcm)
    windows = media.timing_audio_windows(source, tmp_path, 5)
    recovered = bytearray()
    for path, start, end in windows:
        assert 2.5 <= end - start <= 5
        with wave.open(str(path)) as audio:
            recovered.extend(audio.readframes(audio.getnframes()))
    assert bytes(recovered) == pcm
    assert windows[0][1] == 0
    assert all(a[2] == b[1] for a, b in zip(windows, windows[1:]))


@pytest.mark.asyncio
async def test_weak_short_recognition_gets_one_wider_bodhan_window(tools, run, fake):
    phrases = ["Wash with soap.", "We washed our hands.", "Have you washed yours?", "Remember before meals."]
    parts = [bytes([index + 11, 0]) * (5 * 16000) for index in range(4)]
    pcm = b"".join(parts)
    source = run.input_root / "reminder.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(pcm)
    fake.audio_text[hashlib.sha256(pcm).hexdigest()] = " ".join(phrases)
    for i, (data, phrase) in enumerate(zip(parts, phrases)):
        fake.audio_text[hashlib.sha256(data).hexdigest()] = "unrelated repetition" if i == 1 else phrase
    for i in (0, 2):
        fake.audio_text[hashlib.sha256(b"".join(parts[i:i + 2])).hexdigest()] = (
            " ".join(phrases[i:i + 2]) if i == 0 else "unrelated repetition")
    result = await tools.call("subtitles", {"source": run.register(source, "input"), "source_language": "en"})
    assert result["ok"], result
    assert result["timing_verification"]["requested_timing_window_seconds"] == 5
    assert result["timing_verification"]["max_anchor_window_seconds"] == 10
    assert '"subtitle_recognition_regroup"' in run.trace.read_text()
    audit = __import__("json").loads(run.resolve(result["alignment_ref"]).read_text())
    assert len(audit[0]["recognition_attempts"]) == 2
    assert [(row["start"], row["end"]) for row in audit[0]["recognized"]] == [(0, 10), (10, 15), (15, 20)]
    assert " ".join(row["text"] for row in audit[0]["contextual_source"]) == " ".join(phrases)


@pytest.mark.asyncio
async def test_subtitles_use_recognized_windows_without_rewriting_context(tools, run, fake):
    phrases = ["We are late.", "Come along.", "The wrong way to wash hands."]
    parts = [bytes([index + 1, 0]) * (5 * 16000) for index in range(3)]
    pcm = b"".join(parts)
    source = run.input_root / "dialogue.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(pcm)
    fake.audio_text[hashlib.sha256(pcm).hexdigest()] = " ".join(phrases)
    for data, phrase in zip(parts, phrases):
        fake.audio_text[hashlib.sha256(data).hexdigest()] = phrase
    result = await tools.call("subtitles", {
        "source": run.register(source, "input"), "source_language": "en",
        "timing_window_seconds": 5,
    })
    assert result["ok"], result
    srt = run.resolve(result["ref"]).read_text()
    assert "00:00:10,000 --> 00:00:15,000\nThe wrong way to wash hands." in srt
    assert result["timing_anchors"] == 3
    assert result["timing_verification"]["passed"]
    assert result["verification"]["passed"]


@pytest.mark.asyncio
async def test_failed_isolated_phrase_can_use_checked_adjacent_context(tools, run, monkeypatch):
    async def verify(text, target):
        return text, {"kind": "translation", "checked": True, "passed": text != "Second instruction."}

    monkeypatch.setattr(tools, "_verified_piece", verify)
    anchors = [{"start": i * 5, "end": (i + 1) * 5, "text": text}
               for i, text in enumerate(["First instruction.", "Second instruction.", "Third instruction."])]
    translated, checks = await tools._translated_timing_anchors(anchors, "en")
    assert [(row["start"], row["end"], row["text"]) for row in translated] == [
        (0, 5, "First instruction."), (5, 15, "Second instruction. Third instruction.")]
    assert len(checks) == 2 and all(check["passed"] for check in checks)
    assert '"subtitle_phrase_regroup"' in run.trace.read_text()


@pytest.mark.asyncio
async def test_regrouping_can_cross_an_unassigned_recognition_window(tools, monkeypatch):
    async def verify(text, target):
        return text, {"kind": "translation", "checked": True, "passed": text != "Come, Sam."}

    monkeypatch.setattr(tools, "_verified_piece", verify)
    anchors = [{"start": 0, "end": 5, "text": "Come, Sam."},
               {"start": 10, "end": 15, "text": "Sam is ready."}]
    translated, checks = await tools._translated_timing_anchors(anchors, "en")
    assert [(row["start"], row["end"], row["text"]) for row in translated] == [
        (0, 15, "Come, Sam. Sam is ready.")]
    assert checks[0]["passed"]


@pytest.mark.asyncio
async def test_regrouping_never_releases_unverified_text_or_joins_whole_recording(tools, monkeypatch):
    calls = []

    async def verify(text, target):
        calls.append(text)
        return text, {"kind": "translation", "checked": True, "passed": False}

    monkeypatch.setattr(tools, "_verified_piece", verify)
    anchors = [{"start": i * 5, "end": (i + 1) * 5, "text": str(i)} for i in range(4)]
    with pytest.raises(ToolError, match="verification"):
        await tools._translated_timing_anchors(anchors, "en")
    assert calls == ["0", "0 1"]


@pytest.mark.asyncio
async def test_checked_anchor_translations_can_differ_from_context_wording(tools, run, fake, monkeypatch):
    phrases = ["The correct way to wash hands is", "always by applying soap and rubbing the palms", "between the fingers."]
    parts = [bytes([index + 7, 0]) * (5 * 16000) for index in range(3)]
    pcm = b"".join(parts)
    source = run.input_root / "instructions.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(pcm)
    fake.audio_text[hashlib.sha256(pcm).hexdigest()] = " ".join(phrases)
    for data, phrase in zip(parts, phrases):
        fake.audio_text[hashlib.sha256(data).hexdigest()] = phrase

    async def checked_translations(anchors, target):
        wording = ["The right way to wash hands", "Always rub palms with soap", "Between fingers"]
        return ([{**row, "text": text} for row, text in zip(anchors, wording)],
                [{"kind": "translation", "checked": True, "passed": True}])

    monkeypatch.setattr(tools, "_translated_timing_anchors", checked_translations)
    result = await tools.call("subtitles", {"source": run.register(source, "input"), "source_language": "en"})
    assert result["ok"], result
    assert "00:00:05,000 --> 00:00:10,000\nAlways rub palms with soap" in run.resolve(result["ref"]).read_text()


@pytest.mark.asyncio
async def test_optional_model_pacing_does_not_delay_cache_hits(run, monkeypatch):
    monkeypatch.setenv("BODHAN_MIN_INTERVAL_SECONDS", "0.01")
    gateway = Gateway(run, httpx.MockTransport(lambda request: httpx.Response(200, json={"text": "ok"})))
    kwargs = dict(model="indic-transcribe", url="https://api.bodhan.ai/test", key_env="BODHAN_TRANSCRIBE_KEY", reserve=Decimal(0))
    await gateway.request(**kwargs, payload={"i": 0})
    await gateway.request(**kwargs, payload={"i": 1})
    await gateway.request(**kwargs, payload={"i": 0})
    assert run.trace.read_text().count('"kind": "model_pacing"') == 1
    await gateway.close()


@pytest.mark.asyncio
async def test_invalid_model_pacing_never_reserves_or_dispatches(run, monkeypatch):
    monkeypatch.setenv("BODHAN_MIN_INTERVAL_SECONDS", "nan")
    gateway = Gateway(run, httpx.MockTransport(lambda request: pytest.fail("must not dispatch")))
    with pytest.raises(ToolError, match="interval"):
        await gateway.request(model="indic-transcribe", url="https://api.bodhan.ai/test", key_env="BODHAN_TRANSCRIBE_KEY", payload={}, reserve=Decimal("0.1"))
    assert run.cost == 0 and not run.calls
    await gateway.close()
