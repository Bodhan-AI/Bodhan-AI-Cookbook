"""Check VB-Cable end to end without Meet, Zoom or Bodhan.

    python -m tools.cable_check

Plays a 1 s tone into CABLE_DEVICE ("CABLE Input") and records the cable's other end
("CABLE Output", the device Meet/Zoom will use as its microphone). Silent on your speakers.
Also reports the Windows defaults, which the VB-Cable installer likes to change.
"""
from __future__ import annotations

import asyncio
import os

import numpy as np
from dotenv import load_dotenv

from app.audio.capture import (AudioSource, default_output_device, find_input_device, find_output_device,
                               is_virtual_cable, pyaudio_module)
from app.audio.playback import Player
from app.audio.wavutil import SAMPLE_RATE, encode_wav

TONE_HZ = 440
SETTLE_S = 0.3
TONE_RATE = 24000  # same rate as Bodhan TTS output, so playback resamples the same way


def tone(seconds: float = 1.0) -> bytes:
    t = np.arange(int(TONE_RATE * seconds)) / TONE_RATE
    return encode_wav((0.3 * np.sin(2 * np.pi * TONE_HZ * t)).astype(np.float32), TONE_RATE)


async def check(cable_name: str, record_name: str) -> int:
    pyaudio = pyaudio_module()
    pa = pyaudio.PyAudio()
    ok = True
    try:
        wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
        out_default = default_output_device(pa)["name"]
        in_default = pa.get_device_info_by_index(wasapi["defaultInputDevice"])["name"]
        print(f"Windows default output: {out_default}")
        print(f"Windows default input:  {in_default}")
        if is_virtual_cable(out_default):
            ok = False
            print("  FIX: set the default output back to your speakers/headphones "
                  "(Settings > System > Sound > Output). Subtitles won't start otherwise.")
        if is_virtual_cable(in_default) and not os.getenv("MIC_DEVICE"):
            ok = False
            print("  FIX: set the default input back to your real microphone (Settings > System > Sound > Input), "
                  "or set MIC_DEVICE in .env. Interpreting stays off otherwise.")

        player = Player(pa, find_output_device(pa, cable_name))
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        source = AudioSource(pa, find_input_device(pa, record_name), loop, queue)
        source.start()
        try:
            await asyncio.sleep(SETTLE_S)  # let the capture stream settle
            started = loop.time()
            playing = asyncio.create_task(asyncio.to_thread(player.play_wav, tone()))
            blocks: list[np.ndarray] = []
            while loop.time() - started < 2.0:
                try:
                    blocks.append(await asyncio.wait_for(queue.get(), 0.25))
                except asyncio.TimeoutError:
                    blocks.append(np.zeros(SAMPLE_RATE // 4, dtype=np.float32))
            await playing
        finally:
            source.stop()
            player.close()
    finally:
        pa.terminate()

    audio = np.concatenate(blocks)
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    loud = np.flatnonzero(np.abs(audio) > 0.05)
    print(f"\nPlayed a {TONE_HZ} Hz tone into {player.name!r}; recorded {source.name!r}.")
    if peak < 0.05:
        print(f"FAIL: nothing came through the cable (peak {peak:.3f}). Reboot, or reinstall VB-Cable as admin.")
        return 1
    heard_s = len(loud) / SAMPLE_RATE
    print(f"PASS: tone came through (peak {peak:.2f}, ~{heard_s:.1f} s heard, "
          f"cable delay ~{max(0.0, loud[0] / SAMPLE_RATE - SETTLE_S) * 1000:.0f} ms).")
    return 0 if ok else 2


def main() -> None:
    load_dotenv()
    cable = os.getenv("CABLE_DEVICE") or "CABLE Input"
    raise SystemExit(asyncio.run(check(cable, "CABLE Output")))


if __name__ == "__main__":
    main()
