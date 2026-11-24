import io

import numpy as np
import pytest
import soundfile as sf

from bodhan_eval.config import Prices, Settings
from bodhan_eval.infra import Cache, CostLedger


@pytest.fixture
def settings():
    return Settings(
        bodhan_base_url="https://api.test",
        translate_key="t-key",
        speak_key="s-key",
        transcribe_key="a-key",
        llm_base_url="https://llm.test/v1",
        llm_model="judge-model",
        llm_api_key="l-key",
    )


@pytest.fixture
def cache(tmp_path):
    c = Cache(tmp_path / "cache.sqlite")
    yield c
    c.close()


@pytest.fixture
def ledger(cache):
    return CostLedger(Prices(), max_run_inr=10.0, spend_sink=cache.add_spend)


def make_wav(seconds: float = 2.0, sr: int = 24000, freq: float = 220.0) -> bytes:
    t = np.arange(int(seconds * sr)) / sr
    buf = io.BytesIO()
    sf.write(buf, 0.3 * np.sin(2 * np.pi * freq * t), sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()
