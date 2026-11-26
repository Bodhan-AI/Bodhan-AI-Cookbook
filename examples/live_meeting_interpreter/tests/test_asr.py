import asyncio
import threading
import time

import numpy as np
import pytest

from app.asr import HostedTranscriber, LocalTranscriber
from app.audio.wavutil import decode_wav
from app.bodhan.client import BodhanError
from app.bodhan.languages import ASR_LANGUAGES, LOCAL_ASR_LANGUAGES
from app.config import ConfigError, load_settings

AUDIO = np.zeros(1600, dtype=np.float32)


def test_hosted_sends_wav_to_the_client():
    class Client:
        async def transcribe(self, wav, language, deadline=None):
            self.got = (decode_wav(wav)[1], language, deadline)
            return "नमस्ते"

    client = Client()
    assert asyncio.run(HostedTranscriber(client).transcribe(AUDIO, "hi", 5.0)) == "नमस्ते"
    assert client.got == (16000, "hi", 5.0)


def test_local_runs_one_at_a_time_off_the_event_loop():
    loop_thread = threading.get_ident()
    active, seen = [0], []

    def model(audio, lang):
        active[0] += 1
        seen.append((active[0], threading.get_ident() != loop_thread, lang))
        time.sleep(0.05)
        active[0] -= 1
        return f" {lang} text "

    async def go():
        asr = LocalTranscriber(model)
        return await asyncio.gather(*(asr.transcribe(AUDIO, lang) for lang in ("hi", "en", "ta")))

    assert asyncio.run(go()) == ["hi text", "en text", "ta text"]  # stripped, in order
    assert seen == [(1, True, "hi"), (1, True, "en"), (1, True, "ta")]  # never concurrent, never on the loop


def test_local_skips_work_past_its_deadline():
    calls = []
    now = [100.0]
    asr = LocalTranscriber(lambda audio, lang: calls.append(lang) or "x", clock=lambda: now[0])
    with pytest.raises(BodhanError) as err:
        asyncio.run(asr.transcribe(AUDIO, "hi", deadline=99.0))
    assert err.value.code == "deadline_exceeded" and calls == []
    assert asyncio.run(asr.transcribe(AUDIO, "hi", deadline=101.0)) == "x"


def test_local_model_accepts_every_hosted_asr_language():
    assert ASR_LANGUAGES <= LOCAL_ASR_LANGUAGES


# ---- config

KEYS = ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_API_KEY",
        "ASR_BACKEND", "LOCAL_ASR_MODEL")


@pytest.fixture
def env(monkeypatch, tmp_path):
    for name in KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BODHAN_TRANSLATE_KEY", "t")
    monkeypatch.setenv("BODHAN_SPEAK_KEY", "s")
    return monkeypatch, str(tmp_path / "none.env")


def test_hosted_backend_needs_the_transcribe_key(env):
    _, path = env
    with pytest.raises(ConfigError, match="transcribe"):
        load_settings(path)


def test_local_backend_does_not_need_the_transcribe_key(env):
    monkeypatch, path = env
    monkeypatch.setenv("ASR_BACKEND", "Local")
    s = load_settings(path)
    assert (s.asr_backend, s.transcribe_key, s.local_asr_model) == ("local", "", "models/indic-transcribe-core")


def test_dubbing_settings(env):
    monkeypatch, path = env
    monkeypatch.setenv("BODHAN_TRANSCRIBE_KEY", "a")
    s = load_settings(path)
    assert (s.dub_incoming, s.dub_device, s.dub_voice) == (False, None, "Kavya")  # off; voice for English
    monkeypatch.setenv("DUB_INCOMING", "true")
    monkeypatch.setenv("DUB_DEVICE", "Headphones")
    monkeypatch.setenv("DUB_VOICE", "nobody")
    with pytest.raises(ConfigError, match="DUB_VOICE"):
        load_settings(path)


def test_unknown_backend_is_rejected(env):
    monkeypatch, path = env
    monkeypatch.setenv("ASR_BACKEND", "gpu")
    with pytest.raises(ConfigError, match="ASR_BACKEND"):
        load_settings(path)
