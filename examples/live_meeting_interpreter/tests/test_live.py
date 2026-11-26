"""Live captions (ASR_BACKEND=local): provisional captions while the speaker is still talking."""
import asyncio

import numpy as np
import pytest

from app.asr import Busy, LocalTranscriber
from app.audio.vad import Segment
from app.config import RateLimits
from app.pipeline.common import Caption, InOrder
from app.pipeline.incoming import LIVE_TRANSLATE_RESERVE, IncomingSubtitles
from app.ui.overlay import PENDING, translated_line
from tests.test_client import settings
from tests.test_outgoing import FakeUI

SR = 16000


def seg(seq, seconds, started, ended):
    return Segment(seq, np.zeros(int(seconds * SR), dtype=np.float32), started, ended)


class LiveClient:
    """Client + transcriber. Transcripts are keyed by audio length; gates hold calls back."""

    def __init__(self, texts, live_gate=None, translate_gates=None, spare=99):
        self.texts = texts
        self.live_gate = live_gate
        self.translate_gates = translate_gates or {}
        self.spare = spare
        self.background_calls = 0
        self.translated: list[str] = []

    def headroom(self, model):
        assert model == "translate"
        return self.spare

    async def transcribe(self, audio, language, deadline=None, background=False):
        if background:
            self.background_calls += 1
            if self.live_gate:
                await self.live_gate.wait()
        return self.texts[len(audio)]

    async def translate(self, text, target_language, script="native", deadline=None):
        self.translated.append(text)
        if text in self.translate_gates:
            await self.translate_gates[text].wait()
        return f"EN({text})"


class ScriptedSegmenter:
    """Each push plays one step: (segments that end now, utterance in progress afterwards)."""

    in_speech = True

    def __init__(self, steps):
        self.steps = list(steps)
        self._current = None

    def push(self, block):
        emit, self._current = self.steps.pop(0) if self.steps else ([], None)
        return emit

    def current(self):
        return self._current


def run_live(client, steps, finals, after=None, **kw):
    """Feed one block per step (with time for tasks to run), call after[i]() after step i."""
    async def go():
        ui = FakeUI()
        s = settings(**{"asr_backend": "local", "live_caption_interval_s": 0.01, **kw})
        sub = IncomingSubtitles(s, client, client, ui, ScriptedSegmenter(steps))
        audio, stop = asyncio.Queue(), asyncio.Event()
        task = asyncio.create_task(sub.run(audio, stop))
        for i in range(len(steps)):
            audio.put_nowait(np.zeros(10, dtype=np.float32))
            await asyncio.sleep(0.05)
            if after and i in after:
                after[i]()
                await asyncio.sleep(0.05)
        for _ in range(300):
            if sum(not c.pending and not c.live for c in ui.captions) >= finals:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        stop.set()
        await task
        return ui

    return asyncio.run(go())


SO_FAR = seg(1, 4.0, 0.0, 4.0)   # 4 s into the sentence
DONE = seg(1, 6.0, 0.0, 6.6)     # the sentence ended
TEXTS = {len(SO_FAR.audio): "आधा वाक्य", len(DONE.audio): "पूरा वाक्य"}


def test_live_caption_then_final_in_place():
    client = LiveClient(TEXTS)
    ui = run_live(client, [([], SO_FAR), ([DONE], None)], finals=1)
    assert ui.captions == [
        Caption("in", "आधा वाक्य", "EN(आधा वाक्य)", 1, live=True),
        Caption("in", "पूरा वाक्य", "", 1, pending=True),
        Caption("in", "पूरा वाक्य", "EN(पूरा वाक्य)", 1),
    ]


def test_live_result_is_dropped_once_the_sentence_ended():
    gate = asyncio.Event()
    client = LiveClient(TEXTS, live_gate=gate)
    ui = run_live(client, [([], SO_FAR), ([DONE], None)], finals=1, after={1: gate.set})
    assert client.background_calls == 1
    assert not any(c.live for c in ui.captions)
    assert "आधा वाक्य" not in client.translated  # dropped before spending a translation


def test_live_waits_for_earlier_captions_to_be_shown():
    first = seg(1, 3.0, 0.0, 3.6)
    second_so_far = seg(2, 4.0, 10.0, 14.0)  # long pause: not a continuation
    gate = asyncio.Event()
    client = LiveClient({len(first.audio): "पहला", len(second_so_far.audio): "दूसरा"},
                        translate_gates={"पहला": gate})
    ui = run_live(client, [([first], None), ([], second_so_far)], finals=1, after={1: gate.set})
    assert not any(c.live for c in ui.captions)  # #1 was still on its way when #2's live result came
    assert ui.captions[-1] == Caption("in", "पहला", "EN(पहला)", 1)


def test_short_sentences_get_no_live_caption():
    client = LiveClient({len(seg(1, 2.0, 0, 2).audio): "छोटा"})
    run_live(client, [([], seg(1, 2.0, 0.0, 2.0)), ([seg(1, 2.0, 0.0, 2.6)], None)], finals=1)
    assert client.background_calls == 0


def test_live_captions_leave_translations_for_final_captions():
    limits = RateLimits(rpm=50, concurrency=2, max_retries=6, translate_rpm=15)
    client = LiveClient(TEXTS, spare=7)  # half of 15 is reserved for final captions
    ui = run_live(client, [([], SO_FAR), ([DONE], None)], finals=1, limits=limits)
    assert client.background_calls == 0 and not any(c.live for c in ui.captions)
    assert client.translated == ["पूरा वाक्य"]  # the final caption still gets its translation
    client = LiveClient(TEXTS, spare=8)
    ui = run_live(client, [([], SO_FAR), ([DONE], None)], finals=1, limits=limits)
    assert any(c.live for c in ui.captions)


def test_reserve_never_drops_below_the_minimum():
    limits = RateLimits(rpm=50, concurrency=2, max_retries=6, translate_rpm=6)
    client = LiveClient(TEXTS, spare=LIVE_TRANSLATE_RESERVE)
    run_live(client, [([], SO_FAR), ([DONE], None)], finals=1, limits=limits)
    assert client.background_calls == 0


def test_hosted_backend_has_no_live_captions():
    client = LiveClient(TEXTS)
    run_live(client, [([], SO_FAR), ([DONE], None)], finals=1, asr_backend="hosted")
    assert client.background_calls == 0


def test_live_continuation_joins_the_previous_sentence():
    first = seg(1, 2.0, 0.0, 2.6)
    so_far = seg(2, 3.0, 2.8, 5.8)  # 0.8 s pause: continues #1
    client = LiveClient({len(first.audio): "अभी के लिए मैं", len(so_far.audio): "कॉल कट"})
    ui = run_live(client, [([first], None), ([], so_far)], finals=1)
    live = [c for c in ui.captions if c.live]
    assert live == [Caption("in", "अभी के लिए मैं कॉल कट", "EN(अभी के लिए मैं कॉल कट)", 2, replaces=1, live=True)]


# ---- building blocks

def test_background_transcription_gives_way_when_busy():
    release = asyncio.Event()

    async def go():
        asr = LocalTranscriber(lambda audio, lang: "x")
        await asr._queue.acquire()  # a final transcription is running
        with pytest.raises(Busy):
            await asr.transcribe(np.zeros(10, np.float32), "hi", background=True)
        asr._queue.release()
        return await asr.transcribe(np.zeros(10, np.float32), "hi", background=True)

    assert asyncio.run(go()) == "x"
    assert not release.is_set()


def test_in_order_reports_idle_only_after_delivery():
    async def go():
        gate = asyncio.Event()
        shown = []

        async def sink(x):
            await gate.wait()
            shown.append(x)

        runner = InOrder(sink, lambda exc: None)
        consumer = asyncio.create_task(runner.run())
        assert runner.idle
        runner.submit(asyncio.sleep(0, result="a"))
        await asyncio.sleep(0.01)
        busy_while_sinking = not runner.idle
        gate.set()
        await asyncio.sleep(0.01)
        consumer.cancel()
        return busy_while_sinking, runner.idle, shown

    assert asyncio.run(go()) == (True, True, ["a"])


def test_overlay_live_and_its_final():
    live = Caption("in", "आधा", "half", 3, live=True)
    assert translated_line(live, None) == (f"half {PENDING}", False)
    pending_final = Caption("in", "पूरा", "", 3, pending=True)
    assert translated_line(pending_final, live) == (f"half {PENDING}", True)  # keeps the live text
