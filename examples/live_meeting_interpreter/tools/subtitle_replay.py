"""Replay a recording through the subtitle pipeline at real-time pace; print a caption timeline.

    python -m tools.subtitle_replay out/probe.wav [--log-file out/replay.log]
    $env:ASR_BACKEND = "local"; python -m tools.subtitle_replay out/probe.wav   # with live captions

Uses the real VAD, the configured ASR backend and real translation requests (they count against
Bodhan's limits: about one per sentence, plus live captions in local mode). No audio devices.
Records a session like the app does (SESSION_RECORD), so you get the Markdown and SRT too.
Then: python -m tools.latency_report out/replay.log
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time
from pathlib import Path

import numpy as np
import soundfile as sf

from app.asr import HostedTranscriber, LocalTranscriber, Transcriber
from app.audio.vad import make_segmenter
from app.bodhan.client import BodhanClient
from app.config import Settings, load_settings
from app.pipeline.common import UI, Caption
from app.pipeline.incoming import IncomingSubtitles
from app.session import RecordingUI, SessionRecorder

SR = 16000
BLOCK = SR // 10  # 100 ms, like the capture callbacks
log = logging.getLogger("app")


class TimelineUI:
    def __init__(self) -> None:
        self.t0 = time.monotonic()

    def show_caption(self, c: Caption) -> None:
        kind = "live " if c.live else ("…    " if c.pending else "final")
        print(f"{time.monotonic() - self.t0:6.1f}s #{c.seq:<3} {kind} | {c.translated or c.original}", flush=True)

    def set_status(self, text: str, active: bool = False) -> None:
        print(f"status: {text}", flush=True)


async def transcriber(s: Settings, client: BodhanClient) -> Transcriber:
    if s.asr_backend == "local":
        log.info("Speech recognition: local Indic-Transcribe from %s", s.local_asr_model)
        return await asyncio.to_thread(LocalTranscriber.load, s.local_asr_model, s.their_language)
    log.info("Speech recognition: Bodhan hosted Indic-Transcribe")
    return HostedTranscriber(client)


async def replay(path: Path, tail_s: float) -> None:
    s = load_settings()
    audio, sr = sf.read(path, dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if sr != SR:
        import soxr
        audio = soxr.resample(audio, sr, SR).astype(np.float32)
    client = BodhanClient(s)
    recorder = (SessionRecorder.create(s.session_dir, {
        "my_language": s.my_language, "their_language": s.their_language, "asr_backend": s.asr_backend,
        "mode": "replay", "source": str(path)}) if s.record_sessions else None)
    try:
        asr = await transcriber(s, client)
        ui = TimelineUI()
        shown: UI = RecordingUI(ui, recorder) if recorder else ui
        sub = IncomingSubtitles(s, client, asr, shown, make_segmenter(s.vad))
        q: asyncio.Queue = asyncio.Queue()
        stop = asyncio.Event()
        task = asyncio.create_task(sub.run(q, stop))
        ui.t0 = start = time.monotonic()
        for i in range(0, len(audio), BLOCK):
            q.put_nowait(audio[i:i + BLOCK])
            await asyncio.sleep(max(0.0, start + (i + BLOCK) / SR - time.monotonic()))
        await asyncio.sleep(tail_s)  # the pipeline feeds silence so the last sentence can end
        stop.set()
        await task
    finally:
        await client.aclose()
        if recorder:
            for saved in recorder.close():
                print(f"saved {saved}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a recording through the subtitle pipeline")
    parser.add_argument("wav", type=Path)
    parser.add_argument("--log-file", default="out/replay.log", help="appended to, like the app's")
    parser.add_argument("--tail", type=float, default=12.0, help="seconds to wait after the audio ends")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(args.log_file, encoding="utf-8")])
    asyncio.run(replay(args.wav, args.tail))


if __name__ == "__main__":
    main()
