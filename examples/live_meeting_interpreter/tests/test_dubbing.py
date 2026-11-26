import asyncio

from app.config import RateLimits
from app.pipeline.common import Caption
from app.pipeline.dubbing import DUB_MAX_LAG_S, DubbingUI, IncomingDubber
from tests.test_client import settings
from tests.test_outgoing import FakeUI

NOW = 100.0


class FakeSpeech:
    def __init__(self, spare=99):
        self.spare = spare
        self.requests: list[tuple[str, str, str]] = []

    def headroom(self, model):
        assert model == "speak"
        return self.spare

    async def speak(self, text, lang, voice, style=None, deadline=None):
        self.requests.append((text, lang, voice))
        return text.encode()


class FakePlayer:
    def __init__(self):
        self.played: list[str] = []

    def play_wav(self, wav):
        self.played.append(wav.decode())


def cap(seq, text, end=NOW - 1.0, **kw):
    return Caption("in", f"मूल {seq}", text, seq, start=end - 2.0, end=end, **kw)


def dub(captions, enabled=True, spare=99, limits=None, between=None):
    """Offer captions, let the dubber speak them; between(dubber) runs after the offers."""
    async def go():
        s = settings(dub_voice="Kavya", **({"limits": limits} if limits else {}))
        client, player = FakeSpeech(spare), FakePlayer()
        dubber = IncomingDubber(s, client, player, enabled, clock=lambda: NOW)
        stop = asyncio.Event()
        task = asyncio.create_task(dubber.run(stop))
        for c in captions:
            dubber.offer(c)
        if between:
            between(dubber)
        await asyncio.sleep(0.2)
        stop.set()
        await task
        return dubber, client, player

    return asyncio.run(go())


def test_final_incoming_translations_are_spoken_in_order():
    _, client, player = dub([cap(1, "Hello"),
                             cap(2, "", pending=True),                       # transcript only
                             cap(2, "half", live=True),                     # provisional
                             Caption("out", "Thanks", "धन्यवाद", 1, end=NOW),   # your own side
                             cap(2, "How are you?")])
    assert player.played == ["Hello", "How are you?"]
    assert client.requests[0] == ("Hello", "en", "Kavya")  # spoken in MY_LANGUAGE


def test_merged_caption_replaces_its_waiting_fragment():
    dubber, _, player = dub([cap(4, "for a year"), cap(5, "if not needed for a year", replaces=4)],
                            between=None)
    # #4 may already be playing when #5 arrives; but while it's still queued it is dropped.
    assert player.played[-1] == "if not needed for a year"
    assert player.played.count("for a year") <= 1


def test_merge_drops_a_fragment_that_is_still_queued():
    async def go():
        client, player = FakeSpeech(), FakePlayer()
        dubber = IncomingDubber(settings(), client, player, True, clock=lambda: NOW)
        dubber.offer(cap(4, "for a year"))
        dubber.offer(cap(5, "if not needed for a year", replaces=4))  # before run() picks #4
        stop = asyncio.Event()
        task = asyncio.create_task(dubber.run(stop))
        await asyncio.sleep(0.1)
        stop.set()
        await task
        return player.played

    assert asyncio.run(go()) == ["if not needed for a year"]


def test_lines_too_far_behind_are_skipped():
    _, client, player = dub([cap(1, "old news", end=NOW - DUB_MAX_LAG_S - 1), cap(2, "fresh")])
    assert player.played == ["fresh"] and [r[0] for r in client.requests] == ["fresh"]


def test_interpreting_keeps_its_share_of_speech_requests():
    limits = RateLimits(rpm=50, concurrency=2, max_retries=6, speak_rpm=15)
    # Half of 15 (7) must stay free after this line's one request.
    _, client, player = dub([cap(1, "Hello")], spare=7, limits=limits)  # would leave 6: skipped
    assert player.played == [] and client.requests == []
    _, _, player = dub([cap(1, "Hello")], spare=8, limits=limits)       # leaves 7: spoken
    assert player.played == ["Hello"]


def test_switched_off_it_ignores_captions_and_drops_the_queue():
    _, client, player = dub([cap(1, "Hello")], enabled=False)
    assert player.played == [] and client.requests == []

    _, _, player = dub([cap(1, "one"), cap(2, "two")], between=lambda d: d.set_enabled(False))
    assert player.played == []


def test_long_translation_is_spoken_in_parts_in_order():
    text = ("I had said another thing a year ago, friends, and the countrymen listened to me, "
            "so today the price of gold has fallen and people are telling me that their money was saved.")
    _, client, player = dub([cap(1, text)])
    assert len(client.requests) >= 2
    assert " ".join(player.played) == text


class SlowPlayer(FakePlayer):
    """Playing takes a while, like real speech; records what had been requested by then."""

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.requested_while_playing: list[list[str]] = []

    def play_wav(self, wav):
        import time
        time.sleep(0.15)
        self.requested_while_playing.append([r[0] for r in self.client.requests])
        super().play_wav(wav)


def run_slow(offers, after_first_request=None):
    async def go():
        client = FakeSpeech()
        player = SlowPlayer(client)
        dubber = IncomingDubber(settings(), client, player, True, clock=lambda: NOW)
        stop = asyncio.Event()
        task = asyncio.create_task(dubber.run(stop))
        for c in offers:
            dubber.offer(c)
        await asyncio.sleep(0.05)
        if after_first_request:
            after_first_request(dubber)
        await asyncio.sleep(0.6)
        stop.set()
        await task
        return client, player

    return asyncio.run(go())


def test_next_line_is_synthesised_while_the_current_one_plays():
    client, player = run_slow([cap(1, "one"), cap(2, "two")])
    assert player.played == ["one", "two"]
    assert player.requested_while_playing[0] == ["one", "two"]  # #2 was requested during #1's playback


def test_merge_cancels_a_fragment_already_being_synthesised():
    def merge(dubber):  # #2 is being synthesised while #1 plays; then its merged version arrives
        assert dubber._next is not None and dubber._next.caption.seq == 2
        dubber.offer(cap(3, "two and three", replaces=2))

    _, player = run_slow([cap(1, "one"), cap(2, "two")], after_first_request=merge)
    assert player.played == ["one", "two and three"]


def test_dubbing_ui_passes_everything_on():
    async def go():
        ui = FakeUI()
        dubber = IncomingDubber(settings(), FakeSpeech(), FakePlayer(), True, clock=lambda: NOW)
        wrapped = DubbingUI(ui, dubber)
        wrapped.show_caption(cap(1, "Hello"))
        wrapped.set_status("ok", active=True)
        return ui, list(dubber._queue)

    ui, queued = asyncio.run(go())
    assert [c.seq for c in ui.captions] == [1] and ui.statuses == ["ok"] and [c.seq for c in queued] == [1]
