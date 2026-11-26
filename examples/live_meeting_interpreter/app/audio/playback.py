"""Plays Bodhan TTS audio (PCM16 24 kHz mono WAV) into an output device.

For the meeting, the device is VB-Cable's "CABLE Input"; Meet/Zoom then uses
"CABLE Output" as its microphone. Blocking: call from a worker thread.
"""
from __future__ import annotations

import threading

import numpy as np
import soxr

from app.audio.capture import Device, pyaudio_module
from app.audio.wavutil import decode_wav, float_to_pcm16


class Player:
    def __init__(self, pa, device: Device) -> None:
        self.name = str(device["name"])
        self._channels = min(2, int(device["maxOutputChannels"]))
        self._rate = int(device["defaultSampleRate"])  # WASAPI shared mode wants the mix rate
        self._stream = pa.open(format=pyaudio_module().paInt16, channels=self._channels,
                               rate=self._rate, output=True, output_device_index=int(device["index"]))
        self._lock = threading.Lock()

    def play_wav(self, wav: bytes) -> None:
        audio, rate = decode_wav(wav)
        if rate != self._rate:
            audio = soxr.resample(audio, rate, self._rate)
        pcm = float_to_pcm16(audio)
        if self._channels == 2:
            pcm = np.repeat(pcm, 2)  # interleave L=R
        with self._lock:
            self._stream.write(pcm.tobytes())

    def close(self) -> None:
        with self._lock:
            self._stream.stop_stream()
            self._stream.close()
