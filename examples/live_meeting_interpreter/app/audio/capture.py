"""Windows audio capture (WASAPI) via PyAudioWPatch.

- Meeting audio: WASAPI *loopback* of the default output device, i.e. whatever
  Meet/Zoom plays to your headphones. Your own voice is not in it.
- Your voice: the microphone, only while push-to-talk is held (see `gate`).

Gotcha: WASAPI loopback delivers NO callbacks while nothing is playing, so
consumers must treat a quiet queue as silence (see pipeline/incoming.py).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

import numpy as np
import soxr

from app.audio.wavutil import SAMPLE_RATE

log = logging.getLogger(__name__)
Device = dict[str, Any]


def pyaudio_module():
    try:
        import pyaudiowpatch as pyaudio
    except ImportError as exc:  # pragma: no cover - Windows only
        raise RuntimeError("PyAudioWPatch is required (Windows only): pip install PyAudioWPatch") from exc
    return pyaudio


def _wasapi(pa) -> Device:
    return pa.get_host_api_info_by_type(pyaudio_module().paWASAPI)


def _devices(pa) -> list[Device]:
    return [pa.get_device_info_by_index(i) for i in range(pa.get_device_count())]


def device_names(pa) -> tuple[list[str], list[str]]:
    """Names of the WASAPI (microphones, outputs) the app can use; loopback endpoints excluded."""
    wasapi = _wasapi(pa)
    usable = [d for d in _devices(pa) if d["hostApi"] == wasapi["index"] and not d.get("isLoopbackDevice")]
    inputs = sorted({str(d["name"]) for d in usable if d["maxInputChannels"] > 0})
    outputs = sorted({str(d["name"]) for d in usable if d["maxOutputChannels"] > 0})
    return inputs, outputs


def default_output_device(pa) -> Device:
    return pa.get_device_info_by_index(_wasapi(pa)["defaultOutputDevice"])


def is_virtual_cable(device_name: str) -> bool:
    """True for VB-Audio Virtual Cable endpoints ("CABLE Input", "CABLE Output", "CABLE In 16ch")."""
    name = device_name.lower()
    return "vb-audio" in name or name.startswith("cable ")


def find_default_loopback(pa) -> Device:
    speakers = default_output_device(pa)
    if speakers.get("isLoopbackDevice"):
        return speakers
    for dev in pa.get_loopback_device_info_generator():
        if speakers["name"] in dev["name"]:
            return dev
    raise RuntimeError(f"No WASAPI loopback device found for default output {speakers['name']!r}")


def find_input_device(pa, name_contains: Optional[str] = None) -> Device:
    wasapi = _wasapi(pa)
    if name_contains:
        for d in _devices(pa):
            if (d["hostApi"] == wasapi["index"] and d["maxInputChannels"] > 0
                    and not d.get("isLoopbackDevice") and name_contains.lower() in d["name"].lower()):
                return d
        raise RuntimeError(f"No microphone matching MIC_DEVICE={name_contains!r}; run tools/list_devices.py")
    return pa.get_device_info_by_index(wasapi["defaultInputDevice"])


def find_output_device(pa, name_contains: str) -> Device:
    wasapi = _wasapi(pa)
    for d in _devices(pa):
        if (d["hostApi"] == wasapi["index"] and d["maxOutputChannels"] > 0
                and not d.get("isLoopbackDevice") and name_contains.lower() in d["name"].lower()):
            return d
    raise RuntimeError(
        f"Output device {name_contains!r} not found. Install VB-Audio Virtual Cable "
        "(vb-audio.com/Cable) and reboot, or set CABLE_DEVICE."
    )


class AudioSource:
    """Streams a device into an asyncio.Queue as 16 kHz mono float32 blocks."""

    def __init__(self, pa, device: Device, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue,
                 gate: Optional[Callable[[], bool]] = None, block_ms: int = 30) -> None:
        self._pa = pa
        self._pyaudio = pyaudio_module()
        self._device = device
        self._loop = loop
        self._queue = queue
        self._gate = gate
        self._channels = max(1, int(device["maxInputChannels"]))
        self._rate = int(device["defaultSampleRate"])
        self._block = int(self._rate * block_ms / 1000)
        self._resampler = soxr.ResampleStream(self._rate, SAMPLE_RATE, 1, dtype="float32")
        self._stream = None

    @property
    def name(self) -> str:
        return str(self._device["name"])

    def start(self) -> None:
        self._stream = self._pa.open(
            format=self._pyaudio.paInt16, channels=self._channels, rate=self._rate, input=True,
            input_device_index=int(self._device["index"]), frames_per_buffer=self._block,
            stream_callback=self._callback,
        )
        log.info("Capturing %s (%d Hz, %d ch)", self.name, self._rate, self._channels)

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop_stream()
                self._stream.close()
            finally:
                self._stream = None

    def _callback(self, in_data, frame_count, time_info, status):  # PortAudio thread
        try:
            if self._gate is None or self._gate():
                x = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
                if self._channels > 1:
                    x = x.reshape(-1, self._channels).mean(axis=1).astype(np.float32)
                y = self._resampler.resample_chunk(x)
                if y.size:
                    self._loop.call_soon_threadsafe(self._queue.put_nowait, y)
        except RuntimeError:
            pass  # event loop closed during shutdown
        except Exception:  # never let an exception kill the audio thread
            log.exception("Audio callback error on %s", self.name)
        return (None, self._pyaudio.paContinue)
