import asyncio
import threading
import time

import numpy as np

from app.audio.vad import Segment
from app.bodhan.client import BodhanError
from app.pipeline.outgoing import OutgoingInterpreter
from tests.test_client import settings

SOURCE = "Today I did not have dinner because my sister ate my food"
HINDI = "आज मैंने रात का खाना नहीं खाया क्योंकि मेरी बहन ने मेरा खाना खा लिया।"
FIRST, SECOND = "आज मैंने रात का खाना नहीं खाया", "क्योंकि मेरी बहन ने मेरा खाना खा लिया।"


class FakeClient:
    """Speech for every chunk after the first waits until the test releases it."""

    def __init__(self, fail_later: bool = False) -> None:
        self.release_later = asyncio.Event()
        self.fail_later = fail_later

    async def transcribe(self, wav, language, deadline=None):
        return SOURCE

    async def translate(self, text, target_language, script="native", deadline=None):
        return HINDI

    async def speak(self, text, lang, voice, style=None, deadline=None):
        if text != FIRST:
            await self.release_later.wait()
            if self.fail_later:
                raise BodhanError(500, "internal_error", "boom")
        return text.encode()


class FakePlayer:
    def __init__(self):
        self.played: list[str] = []

    def play_wav(self, wav):
        self.played.append(wav.decode())


class FakeUI:
    def __init__(self):
        self.captions, self.statuses = [], []

    def show_caption(self, caption):
        self.captions.append(caption)

    def set_status(self, text, active=False):
        self.statuses.append(text)


def make(client):
    player, ui = FakePlayer(), FakeUI()
    out = OutgoingInterpreter(settings(), client, client, ui, None, player, None, threading.Event())
    now = time.monotonic()
    return out, player, ui, Segment(1, np.zeros(16000, dtype=np.float32), now - 1, now)


def test_first_part_is_ready_before_the_rest_is_synthesised():
    async def run():
        client = FakeClient()
        out, player, ui, segment = make(client)
        utt = await out._process(segment)
        assert utt.first.decode() == FIRST
        assert len(utt.rest) == 1 and not utt.rest[0].done()  # still being synthesised
        playing = asyncio.create_task(out._play(utt))
        await asyncio.sleep(0.05)
        assert player.played == [FIRST]  # first part plays without waiting for the second
        client.release_later.set()
        await playing
        return player, ui

    player, ui = asyncio.run(run())
    assert player.played == [FIRST, SECOND]
    assert ui.captions[0].translated == HINDI


def test_failed_later_part_is_skipped_without_crashing():
    async def run():
        client = FakeClient(fail_later=True)
        out, player, ui, segment = make(client)
        utt = await out._process(segment)
        client.release_later.set()
        await out._play(utt)
        return player, ui

    player, ui = asyncio.run(run())
    assert player.played == [FIRST]
    assert ui.statuses == ["Part of the translation failed and was skipped"]
