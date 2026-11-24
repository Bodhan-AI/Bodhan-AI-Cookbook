"""Transcription stage (order, skips, failures) and the disk cache (keys, hits cost nothing)."""

import numpy as np
from fakes import FakeSession, response

from bodhan_dub.bodhan import BodhanClient, BodhanError, wav_duration
from bodhan_dub.cache import DiskCache
from bodhan_dub.chunking import Chunk
from bodhan_dub.config import Settings
from bodhan_dub.transcribe import clip_wav, transcribe_chunks
from bodhan_dub.vad import SAMPLE_RATE

SETTINGS = Settings.from_env({"BODHAN_API_KEY": "test-key"})


class ScriptedClient:
    """Answers transcribe() by chunk duration, so parallel calls map back to known chunks."""

    def __init__(self, by_duration):
        self.by_duration = by_duration

    def transcribe(self, wav, language="en"):
        answer = self.by_duration[round(wav_duration(wav))]
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_clip_wav_has_the_requested_length():
    samples = np.zeros(10 * SAMPLE_RATE, dtype=np.float32)
    assert wav_duration(clip_wav(samples, 2.0, 5.5)) == 3.5


def test_segments_keep_order_and_skips_are_explained():
    samples = np.zeros(30 * SAMPLE_RATE, dtype=np.float32)
    chunks = [Chunk(0, 2), Chunk(3, 6, forced_cut=True), Chunk(7, 11), Chunk(12, 17)]
    client = ScriptedClient({2: "Hello class.", 3: "  ", 4: BodhanError(503, "internal_error", "down"),
                             5: "Today: angles."})
    segs = transcribe_chunks(client, samples, chunks, max_workers=4)
    assert [s.id for s in segs] == ["s0000", "s0001", "s0002", "s0003"]
    assert [s.text for s in segs] == ["Hello class.", "  ", "", "Today: angles."]
    assert segs[0].skipped is None and segs[3].skipped is None
    assert segs[1].skipped == "no speech recognised" and segs[1].forced_cut
    assert segs[2].skipped.startswith("transcription failed: 503")
    assert (segs[2].start, segs[2].end) == (7, 11)


def test_cache_key_depends_on_model_params_and_payload():
    k = DiskCache.key("m", {"lang": "hi"}, "text")
    assert k == DiskCache.key("m", {"lang": "hi"}, "text")
    assert k != DiskCache.key("m", {"lang": "ta"}, "text")
    assert k != DiskCache.key("m2", {"lang": "hi"}, "text")
    assert k != DiskCache.key("m", {"lang": "hi"}, "text!")


def test_second_identical_call_is_served_from_cache(tmp_path):
    ok = {"translation": "नमस्ते", "truncated": False, "usage": {"completion_tokens": 5}}
    session = FakeSession(response(200, ok))  # only ONE response: a second HTTP call would fail
    client = BodhanClient(SETTINGS, session=session, cache=DiskCache(tmp_path))
    first = client.translate("Hello", "hi")
    second = client.translate("Hello", "hi")
    assert first == second
    usage = client.usage.snapshot()
    assert usage["calls"]["indic-translate"] == 1
    assert usage["cache_hits"]["indic-translate"] == 1

    # A fresh client (a rerun) pays nothing either.
    rerun = BodhanClient(SETTINGS, session=FakeSession(), cache=DiskCache(tmp_path))
    assert rerun.translate("Hello", "hi") == first
    assert rerun.usage.snapshot()["total_cost_inr"] == 0
