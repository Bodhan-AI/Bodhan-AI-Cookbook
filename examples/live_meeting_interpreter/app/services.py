"""Jobs the settings window asks for (device lists, voice preview).

They run on the pipeline's asyncio loop when it is up, so they share its PyAudio instance and
Bodhan client (and with it the rate limiters). If the pipeline isn't running, e.g. it stopped on
a settings error, `run_standalone` gives them a short-lived loop of their own.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, TypeVar

import numpy as np

from app.audio.capture import (default_output_device, device_names, find_input_device, find_output_device,
                               pyaudio_module)
from app.audio.playback import Player
from app.bodhan.client import BodhanClient
from app.config import Settings
from app.pipeline.text import tidy

T = TypeVar("T")
SAMPLE_LINE = "Hello! This is how my voice will sound in the meeting."


@dataclass
class Tools:
    pa: Any
    client: BodhanClient
    quiet: threading.Event = field(default_factory=threading.Event)  # set = subtitles stop listening


Job = Callable[[Tools], Awaitable[T]]


@dataclass
class Devices:
    microphones: list[str]
    outputs: list[str]
    default_mic: str
    default_output: str


async def list_devices(tools: Tools) -> Devices:
    def scan() -> Devices:
        mics, outs = device_names(tools.pa)
        return Devices(mics, outs, str(find_input_device(tools.pa)["name"]),
                       str(default_output_device(tools.pa)["name"]))
    return await asyncio.to_thread(scan)


class VoicePreview:
    """Speak a sample line in a language and voice on your speakers. Remembers the sample's
    translation per language, so repeated previews cost one speech request each."""

    def __init__(self) -> None:
        self._samples: dict[str, str] = {}

    def job(self, lang: str, voice: str, style: Optional[str], device: Optional[str] = None) -> Job[str]:
        """device: an output name to play on (e.g. the dubbing headphones); default = speakers."""
        async def run(tools: Tools) -> str:
            text = self._samples.get(lang)
            if text is None:
                text = SAMPLE_LINE if lang == "en" else tidy(await tools.client.translate(SAMPLE_LINE, lang, "native"))
                self._samples[lang] = text
            wav = await tools.client.speak(text, lang, voice, style)
            target = find_output_device(tools.pa, device) if device else default_output_device(tools.pa)
            player = await asyncio.to_thread(Player, tools.pa, target)
            tools.quiet.set()  # don't subtitle our own preview
            try:
                await asyncio.to_thread(player.play_wav, wav)
            finally:
                tools.quiet.clear()
                await asyncio.to_thread(player.close)
            return text
        return run


CHECKS_FILE = Path("out") / "dub_device_checks.json"  # device name -> leak gain, so each is tested once


def checked_gain(device: str, path: Path = CHECKS_FILE) -> Optional[float]:
    """The remembered leak gain of a device, or None if it hasn't been tested."""
    try:
        return float(json.loads(path.read_text(encoding="utf-8"))[device])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def remember_check(device: str, gain: float, path: Path = CHECKS_FILE) -> None:
    try:
        checks = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        checks = {}
    checks[device] = round(gain, 3)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(checks, indent=1, ensure_ascii=False), encoding="utf-8")


def check_dub_device(device: str) -> Job[float]:
    """Play a short speech clip on `device` while listening to Windows' default output (what the
    subtitles hear). Returns how much of the clip came through: above leak.LEAK_GAIN, dubbing
    there would be transcribed as if the other person said it."""
    async def run(tools: Tools) -> float:
        from app.audio.capture import AudioSource, find_default_loopback
        from app.audio.leak import check_sound, leak_gain
        from app.audio.wavutil import encode_wav

        sound = check_sound()

        loop = asyncio.get_running_loop()
        heard: asyncio.Queue = asyncio.Queue()
        source = await asyncio.to_thread(AudioSource, tools.pa, find_default_loopback(tools.pa), loop, heard)
        player = await asyncio.to_thread(Player, tools.pa, find_output_device(tools.pa, device))
        tools.quiet.set()  # the subtitle pipeline mustn't hear the check
        try:
            source.start()
            await asyncio.sleep(0.3)
            await asyncio.to_thread(player.play_wav, encode_wav(sound))
            await asyncio.sleep(0.6)  # device latency
        finally:
            source.stop()
            tools.quiet.clear()
            await asyncio.to_thread(player.close)
        blocks = []
        while not heard.empty():
            blocks.append(heard.get_nowait())
        return leak_gain(np.concatenate(blocks), sound) if blocks else 0.0
    return run


def run_standalone(settings: Settings, job: Job[T]) -> concurrent.futures.Future[T]:
    """Run a job on its own thread and loop, with its own PyAudio and client."""
    async def main() -> T:
        pa = pyaudio_module().PyAudio()
        client = BodhanClient(settings)
        try:
            return await job(Tools(pa, client))
        finally:
            await client.aclose()
            pa.terminate()

    future: concurrent.futures.Future[T] = concurrent.futures.Future()

    def worker() -> None:
        try:
            future.set_result(asyncio.run(main()))
        except BaseException as exc:  # handed to the window, which shows it
            future.set_exception(exc)

    threading.Thread(target=worker, name="settings-job", daemon=True).start()
    return future
