"""Choosing and loading the speaker model. GPU checks are faked; the real model runs only with RUN_GPU=1."""

import logging
import os

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import diarization
from app.diarization import DiarizationError, build_diarizer
from app.main import create_app
from tests.conftest import FakeBodhan, write_wav


class FakeNemotron:
    def __init__(self, model_id):
        self.model_id = model_id


def test_none_means_no_diarizer(settings):
    assert build_diarizer(settings) is None


def test_unknown_provider_is_an_error(settings):
    settings.diarization_provider = "pyannote"
    with pytest.raises(DiarizationError):
        build_diarizer(settings)


def test_nemotron_turns_itself_off_without_a_usable_gpu(settings, monkeypatch, caplog):
    settings.diarization_provider = "nemotron"
    monkeypatch.setattr(diarization, "check_gpu", lambda min_gb: "no CUDA-capable NVIDIA GPU was found")
    monkeypatch.setattr(diarization, "NemotronDiarizer", lambda _id: pytest.fail("must not load the model"))
    with caplog.at_level(logging.WARNING):
        assert build_diarizer(settings) is None
    assert "Speaker diarization is off: no CUDA-capable NVIDIA GPU was found" in caplog.text


def test_nemotron_loads_when_the_gpu_is_big_enough(settings, monkeypatch):
    settings.diarization_provider = "Nemotron"
    settings.diarization_min_vram_gb = 2.5
    seen = []
    monkeypatch.setattr(diarization, "check_gpu", lambda min_gb: seen.append(min_gb))
    monkeypatch.setattr(diarization, "NemotronDiarizer", FakeNemotron)
    diarizer = build_diarizer(settings)
    assert isinstance(diarizer, FakeNemotron) and diarizer.model_id == "nvidia/Nemotron-3-Diarization"
    assert seen == [2.5]


def test_nemotron_load_failure_leaves_diarization_off(settings, monkeypatch, caplog):
    settings.diarization_provider = "nemotron"
    monkeypatch.setattr(diarization, "check_gpu", lambda min_gb: None)

    def fail(_id):
        raise OSError("couldn't connect to huggingface.co")

    monkeypatch.setattr(diarization, "NemotronDiarizer", fail)
    with caplog.at_level(logging.WARNING):
        assert build_diarizer(settings) is None
    assert "model could not be loaded (OSError" in caplog.text


class FakeDiarizer:
    async def diarize(self, wav_path, duration):
        return []

    async def aclose(self):
        pass


def test_config_reports_diarization_when_a_model_is_loaded(settings):
    with TestClient(create_app(settings, client=FakeBodhan(), diarizer=FakeDiarizer())) as c:
        assert c.get("/api/config").json()["diarization_enabled"] is True


def test_config_reports_off_when_the_gpu_check_fails(settings, monkeypatch):
    settings.diarization_provider = "nemotron"  # asked for, but this machine "has no GPU"
    monkeypatch.setattr(diarization, "check_gpu", lambda min_gb: "no GPU")
    with TestClient(create_app(settings, client=FakeBodhan())) as c:
        assert c.get("/api/config").json()["diarization_enabled"] is False


@pytest.mark.gpu
@pytest.mark.skipif(not os.environ.get("RUN_GPU"), reason="set RUN_GPU=1 to run the real model on the local GPU")
async def test_real_model_on_gpu(settings, tmp_path):
    reason = diarization.check_gpu(settings.diarization_min_vram_gb)
    if reason:
        pytest.skip(reason)
    diarizer = diarization.NemotronDiarizer(settings.diarization_model)
    try:
        silence = np.zeros(16_000 * 5, dtype=np.int16)
        turns = await diarizer.diarize(write_wav(tmp_path / "silence.wav", silence), 5.0)
        assert turns == []  # runs end to end; silence has no speakers
    finally:
        await diarizer.aclose()
