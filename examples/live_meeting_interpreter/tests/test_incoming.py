import asyncio

import numpy as np

from app.audio.vad import Segment
from app.bodhan.client import BodhanError
from app.pipeline.common import Caption
from app.pipeline.incoming import MERGE_MAX_CHARS, IncomingSubtitles, pause_between
from app.ui.overlay import PENDING, translated_line
from tests.test_client import settings
from tests.test_outgoing import FakeUI


class FakeClient:
    """Serves as both client and transcriber. Transcribe returns the scripted texts in call
    order; a gate holds back one call."""

    def __init__(self, transcripts, gates=None, fail_translate=False):
        self.transcripts = list(transcripts)
        self.gates = gates or {}
        self.fail_translate = fail_translate
        self.calls = 0
        self.translated: list[str] = []

    async def transcribe(self, wav, language, deadline=None, background=False):
        assert not background, "hosted-mode tests must not make live requests"
        i = self.calls
        self.calls += 1
        if i in self.gates:
            await self.gates[i].wait()
        return self.transcripts[i]

    async def translate(self, text, target_language, script="native", deadline=None):
        self.translated.append(text)
        if self.fail_translate:
            raise BodhanError(500, "internal_error", "boom")
        return f"EN({text})"


class FakeSegmenter:
    in_speech = False

    def __init__(self, segments):
        self.segments = list(segments)

    def push(self, block):
        return [self.segments.pop(0)] if self.segments else []


def seg(seq, started, ended):
    return Segment(seq, np.zeros(1600, dtype=np.float32), started, ended)


def run_pipeline(client, segments, finals, release=None, **kw):
    """Run IncomingSubtitles until `finals` complete captions were shown; return the UI."""
    async def go():
        ui = FakeUI()
        sub = IncomingSubtitles(settings(**kw), client, client, ui, FakeSegmenter(segments))
        audio, stop = asyncio.Queue(), asyncio.Event()
        for _ in segments:
            audio.put_nowait(np.zeros(10, dtype=np.float32))
        task = asyncio.create_task(sub.run(audio, stop))
        if release:
            await asyncio.sleep(0.05)
            release.set()
        for _ in range(300):
            if sum(not c.pending for c in ui.captions) >= finals:
                break
            await asyncio.sleep(0.01)
        stop.set()
        await task
        return ui

    return asyncio.run(go())


# Pauses: speech ends 0.6 s (VAD_END_SILENCE_MS) before each segment's ended_at.
SHORT_PAUSE = [seg(1, 0.0, 3.0), seg(2, 3.0, 5.0)]    # 0.6 s pause -> continues
LONG_PAUSE = [seg(1, 0.0, 3.0), seg(2, 10.0, 12.0)]   # 7.6 s pause -> separate


def test_pause_between():
    assert abs(pause_between(SHORT_PAUSE[0], SHORT_PAUSE[1], 0.6) - 0.6) < 1e-9


def test_transcript_shows_first_then_translation_in_place():
    ui = run_pipeline(FakeClient(["नमस्ते"]), [seg(1, 0.0, 3.0)], finals=1)
    assert ui.captions == [
        Caption("in", "नमस्ते", "", 1, pending=True),
        Caption("in", "नमस्ते", "EN(नमस्ते)", 1),
    ]


def test_captions_stay_in_order_when_a_later_transcript_is_faster():
    gate = asyncio.Event()
    ui = run_pipeline(FakeClient(["पहला", "दूसरा"], gates={0: gate}), LONG_PAUSE, finals=2, release=gate)
    assert [(c.seq, c.pending) for c in ui.captions] == [(1, True), (1, False), (2, True), (2, False)]


def test_short_pause_merges_into_the_previous_caption():
    client = FakeClient(["अभी के लिए मैं", "कॉल कट करता हूँ"])
    ui = run_pipeline(client, SHORT_PAUSE, finals=2)
    last = ui.captions[-1]
    assert last == Caption("in", "अभी के लिए मैं कॉल कट करता हूँ", "EN(अभी के लिए मैं कॉल कट करता हूँ)", 2,
                           replaces=1)
    assert client.translated == ["अभी के लिए मैं", "अभी के लिए मैं कॉल कट करता हूँ"]


def test_long_pause_does_not_merge():
    ui = run_pipeline(FakeClient(["ठीक है", "फिर मिलेंगे"]), LONG_PAUSE, finals=2)
    assert ui.captions[-1] == Caption("in", "फिर मिलेंगे", "EN(फिर मिलेंगे)", 2)


def test_a_long_previous_utterance_is_not_continued():
    whole = Segment(1, np.zeros(int(3.5 * 16000), dtype=np.float32), 0.0, 3.6)  # a whole sentence
    after = seg(2, 3.6, 5.0)                                                     # 0.6 s pause
    ui = run_pipeline(FakeClient(["अरे नहीं माँ नहीं है", "वो मंदिर गई है"]), [whole, after], finals=2)
    assert ui.captions[-1] == Caption("in", "वो मंदिर गई है", "EN(वो मंदिर गई है)", 2)


def test_a_merge_joins_at_most_two_pieces():
    pieces = [seg(1, 0.0, 2.0), seg(2, 2.0, 4.0), seg(3, 4.0, 6.0)]  # short, 0.6 s pauses
    ui = run_pipeline(FakeClient(["हुकुम", "माँ को समय लग गया", "वो क्या है ना"]), pieces, finals=3)
    finals = [c for c in ui.captions if not c.pending]
    assert [(c.seq, c.original, c.replaces) for c in finals] == [
        (1, "हुकुम", None), (2, "हुकुम माँ को समय लग गया", 1), (3, "वो क्या है ना", None)]


def test_merging_can_be_turned_off():
    ui = run_pipeline(FakeClient(["ठीक है", "फिर मिलेंगे"]), SHORT_PAUSE, finals=2, subtitle_merge_pause_s=0)
    assert ui.captions[-1].replaces is None


def test_merge_stops_at_the_length_cap():
    long_text = "क" * (MERGE_MAX_CHARS - 3)
    ui = run_pipeline(FakeClient([long_text, "और भी"]), SHORT_PAUSE, finals=2)
    assert ui.captions[-1] == Caption("in", "और भी", "EN(और भी)", 2)


def test_noise_before_does_not_merge():
    ui = run_pipeline(FakeClient(["", "फिर मिलेंगे"]), SHORT_PAUSE, finals=1)
    assert ui.captions[-1] == Caption("in", "फिर मिलेंगे", "EN(फिर मिलेंगे)", 2)


def test_failed_translation_keeps_the_transcript():
    ui = run_pipeline(FakeClient(["नमस्ते"], fail_translate=True), [seg(1, 0.0, 3.0)], finals=1)
    assert ui.captions[-1] == Caption("in", "नमस्ते", "", 1)
    assert ui.statuses == ["Subtitle failed: internal_error"]


def test_same_language_needs_no_translation():
    client = FakeClient(["नमस्ते"])
    ui = run_pipeline(client, [seg(1, 0.0, 3.0)], finals=1, my_language="hi")
    assert ui.captions == [Caption("in", "नमस्ते", "नमस्ते", 1)]
    assert client.translated == []


# ---- overlay: what the big line shows

def test_pending_line_shows_placeholder():
    assert translated_line(Caption("in", "नमस्ते", "", 1, pending=True), None) == (PENDING, True)


def test_pending_continuation_keeps_previous_translation():
    shown = Caption("in", "अभी के लिए मैं", "for now i", 1)
    merged = Caption("in", "अभी के लिए मैं कॉल कट करता हूँ", "", 2, pending=True, replaces=1)
    assert translated_line(merged, shown) == (f"for now i {PENDING}", True)
    other = Caption("out", "x", "y", 1)  # same seq but the other direction
    assert translated_line(merged, other) == (PENDING, True)


def test_final_line_shows_translation():
    assert translated_line(Caption("in", "a", "b", 1), None) == ("b", False)
