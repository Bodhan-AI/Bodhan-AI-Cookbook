"""Entry point: python -m app.main [--mode both|subtitles|interpret]

Threads:
  main thread   -> Tk overlay + settings window (Tk must own the main thread)
  worker thread -> asyncio loop: pipelines + Bodhan HTTP calls (one App; replaced on restart)
  PortAudio     -> capture callbacks (hand blocks to asyncio via call_soon_threadsafe)
  pynput        -> push-to-talk listener
"""
from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import dataclasses
import gc
import logging
import os
import sys
import threading
from typing import Callable, Optional, TypeVar

from app.asr import HostedTranscriber, LocalTranscriber, Transcriber
from app.audio.capture import (AudioSource, default_output_device, find_default_loopback,
                               find_input_device, find_output_device, is_virtual_cable, pyaudio_module)
from app.audio.playback import Player
from app.audio.vad import make_segmenter
from app.bodhan.client import BodhanClient
from app.config import ConfigError, Settings, load_settings
from app.bodhan.languages import language_name
from app.hotkey import PushToTalk, key_label
from app.pipeline.incoming import IncomingSubtitles
from app.pipeline.common import UI
from app.pipeline.dubbing import DubbingUI, IncomingDubber
from app.pipeline.outgoing import FLUSH, OutgoingInterpreter
from app.services import (Devices, Job, Tools, VoicePreview, check_dub_device, list_devices, remember_check,
                          run_standalone)
from app.session import RecordingUI, SessionRecorder
from app.settings_store import locked_keys, write_env
from app.stats import LatencyStats
from app.ui.overlay import Overlay

log = logging.getLogger("app")
T = TypeVar("T")


class App:
    def __init__(self, settings: Settings, overlay: Overlay, mode: str,
                 on_dubbing: Optional[Callable[[bool], None]] = None) -> None:
        self.s = settings
        self.ui = overlay
        self.mode = mode
        self._on_dubbing = on_dubbing  # told (from the pipeline thread) whether dubbing is available
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop: Optional[asyncio.Event] = None
        self._tools: Optional[Tools] = None  # set while the pipelines run
        self._dubber: Optional[IncomingDubber] = None
        self._thread = threading.Thread(target=self._thread_main, name="pipeline", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        loop, stop = self._loop, self._stop
        if loop and stop and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(stop.set)
            except RuntimeError:  # the loop closed in between
                pass
        self._thread.join(timeout=timeout)

    def set_dubbing(self, on: bool) -> bool:
        """Switch speaking their translation on/off (any thread). False if it isn't available."""
        dubber = self._dubber
        if dubber is None:
            return False
        dubber.set_enabled(on)
        return True

    def submit(self, job: Job[T]) -> Optional[concurrent.futures.Future[T]]:
        """Run a settings-window job on the pipeline's loop; None if the pipeline isn't running."""
        loop, tools = self._loop, self._tools
        if loop is None or tools is None or loop.is_closed():
            return None
        try:
            return asyncio.run_coroutine_threadsafe(job(tools), loop)
        except RuntimeError:
            return None

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:
            log.exception("Pipeline crashed")
            self.ui.set_status(f"Stopped: {exc}")

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        s, ui, loop = self.s, self.ui, self._loop
        pa = pyaudio_module().PyAudio()
        client = BodhanClient(s)
        sources: list[AudioSource] = []
        players: list[Player] = []
        tasks: list[asyncio.Task] = []
        hotkey: Optional[PushToTalk] = None
        tts_playing = threading.Event()
        tools = Tools(pa, client)  # tools.quiet: set while a voice preview plays on the speakers
        status: list[str] = []
        recorder: Optional[SessionRecorder] = None
        captions: UI = ui  # what the pipelines show captions on

        try:
            self._tools = tools
            if s.record_sessions:
                recorder = SessionRecorder.create(s.session_dir, {
                    "my_language": s.my_language, "their_language": s.their_language,
                    "asr_backend": s.asr_backend, "mode": self.mode})
                captions = RecordingUI(ui, recorder)
                log.info("Recording this session to %s", recorder.path)
            asr = await self._transcriber(client)
            if self.mode in ("both", "subtitles"):
                speakers = default_output_device(pa)
                if s.cable_device.lower() in speakers["name"].lower() or is_virtual_cable(str(speakers["name"])):
                    raise RuntimeError("Windows default output is VB-Cable; set it back to your headphones.")
                dubber = await self._make_dubber(tools, str(speakers["name"]), status, players)
                if self._on_dubbing:
                    self._on_dubbing(dubber is not None)
                if dubber is not None:
                    self._dubber = dubber
                    captions = DubbingUI(captions, dubber)
                    tasks.append(asyncio.create_task(dubber.run(self._stop)))
                q: asyncio.Queue = asyncio.Queue()

                def gate() -> bool:  # PortAudio thread: only reads two flags
                    return not tools.quiet.is_set() and not (s.monitor_tts and tts_playing.is_set())

                src = AudioSource(pa, find_default_loopback(pa), loop, q, gate=gate)
                src.start()
                sources.append(src)
                incoming = IncomingSubtitles(s, client, asr, captions, make_segmenter(s.vad))
                tasks.append(asyncio.create_task(incoming.run(q, self._stop)))
                status.append("Subtitles on" if self.mode == "both"
                              else "Subtitles only (interpreting off: start without --mode)")

            if self.mode in ("both", "interpret"):
                mic_device = find_input_device(pa, s.mic_device)
                if is_virtual_cable(str(mic_device["name"])):
                    # recording the cable we play into would re-translate our own voice forever
                    log.error("Microphone is %r (VB-Cable). Set your real mic as the Windows default "
                              "input, or set MIC_DEVICE in .env.", mic_device["name"])
                    status.append("Interpreting off: mic is VB-Cable, set MIC_DEVICE")
                    meeting = None
                else:
                    try:
                        meeting = Player(pa, find_output_device(pa, s.cable_device))
                    except RuntimeError as exc:
                        log.error("%s", exc)
                        status.append("Interpreting off: VB-Cable not found")
                        meeting = None
                if meeting is not None:
                    players.append(meeting)
                    monitor = Player(pa, default_output_device(pa)) if s.monitor_tts else None
                    if monitor:
                        players.append(monitor)
                    ptt = threading.Event()
                    mq: asyncio.Queue = asyncio.Queue()
                    mic = AudioSource(pa, mic_device, loop, mq, gate=ptt.is_set)
                    mic.start()
                    sources.append(mic)
                    out = OutgoingInterpreter(s, client, asr, captions, make_segmenter(s.vad, push_to_talk=True),
                                              meeting, monitor, tts_playing)
                    tasks.append(asyncio.create_task(out.run(mq, self._stop)))
                    key = key_label(s.ptt_key)
                    idle = f"Hold {key} to talk ({language_name(s.my_language)} → {language_name(s.their_language)})"

                    def pressed() -> None:
                        log.info("Push-to-talk down")
                        ptt.set()
                        ui.set_status(f"Listening… release {key} to send", active=True)

                    def released() -> None:
                        log.info("Push-to-talk up")
                        ptt.clear()
                        loop.call_soon_threadsafe(mq.put_nowait, FLUSH)
                        ui.set_status(idle)

                    hotkey = PushToTalk(s.ptt_key, pressed, released)
                    hotkey.start()
                    status.append(idle)

            ui.set_status(" | ".join(status) + " | right-click for settings")
            await self._stop.wait()
        finally:
            self._tools = None
            self._dubber = None
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if hotkey:
                hotkey.stop()
            for src in sources:
                src.stop()
            for player in players:
                player.close()
            await client.aclose()
            pa.terminate()
            if recorder:
                try:
                    saved = recorder.close()
                    if saved:
                        log.info("Session saved: %s", ", ".join(str(p) for p in saved))
                    else:
                        log.info("Session had no captions; nothing saved")
                except OSError:
                    log.exception("Could not finish the session record %s", recorder.path)

    async def _make_dubber(self, tools: Tools, captured: str, status: list[str],
                           players: list[Player]) -> Optional[IncomingDubber]:
        """The dubber, if a safe DUB_DEVICE is set (it exists even while switched off, so the
        right-click toggle works instantly). None, with a status note, otherwise.

        Safe means: not the captured output, not VB-Cable, and (tested once per device, also
        when set by hand in .env) the subtitle capture can't hear it."""
        s, pa, client = self.s, tools.pa, tools.client
        if not s.dub_device:
            return None
        from app.audio.leak import LEAK_GAIN
        from app.services import check_dub_device, checked_gain, remember_check
        from app.settings_store import dub_device_problems

        try:
            device = find_output_device(pa, s.dub_device)
        except RuntimeError:
            log.error("DUB_DEVICE %r not found; run tools.list_devices", s.dub_device)
            status.append("Speaking their side: device not found")
            return None
        name = str(device["name"])
        problems = dub_device_problems(name, s.cable_device, captured)
        if problems:
            log.error("Not dubbing onto %r: %s", name, problems[0])
            status.append("Speaking their side: off (unsafe device, see Settings)")
            return None
        gain = checked_gain(name)
        if gain is None:
            log.info("Testing that subtitles can't hear %r (a short voice clip on it)", name)
            try:
                gain = await check_dub_device(name)(tools)
            except Exception as exc:
                log.error("Could not test %r: %s", name, exc)
                status.append("Speaking their side: off (device test failed)")
                return None
            remember_check(name, gain)
        if gain > LEAK_GAIN:
            log.error("Not dubbing onto %r: the subtitle capture hears it (gain %.2f)", name, gain)
            status.append("Speaking their side: off (subtitles can hear that device)")
            return None
        player = Player(pa, device)
        players.append(player)
        log.info("Their translation can be spoken on %s (%s)", device["name"], "on" if s.dub_incoming else "off")
        if s.dub_incoming:
            status.append("Speaking their side")
            if s.limits.speak_rpm is None:
                log.warning("BODHAN_SPEAK_RPM is not set; dubbing reserves half of %d speech requests/min for "
                            "interpreting, but the speech key seems to allow ~16. Set BODHAN_SPEAK_RPM=15.",
                            s.limits.rpm_for("speak"))
        return IncomingDubber(s, client, player, s.dub_incoming)

    async def _transcriber(self, client: BodhanClient) -> Transcriber:
        s = self.s
        if s.asr_backend != "local":
            log.info("Speech recognition: Bodhan hosted Indic-Transcribe")
            return HostedTranscriber(client)
        log.info("Speech recognition: local Indic-Transcribe from %s", s.local_asr_model)
        self.ui.set_status("Loading the speech model onto the GPU…")
        # Warm up with the language most utterances will use.
        warmup = s.their_language if self.mode != "interpret" else s.my_language
        return await asyncio.to_thread(LocalTranscriber.load, s.local_asr_model, warmup)


def _free_gpu() -> None:
    """Let a restarted App load the local model again without two copies in 4 GB of VRAM."""
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()


class Controller:
    """Owns the running App and the settings window; restarts the App when settings change.

    Everything here runs on the Tk thread except _restart, which waits for the old App on a
    worker thread so the bar never freezes.
    """

    def __init__(self, overlay: Overlay, settings: Settings, env_file: str, environ: dict[str, str],
                 mode: str, stats: LatencyStats) -> None:
        self.overlay = overlay
        self.settings = settings
        self.env_file = env_file
        self.environ = environ  # the shell's variables: they win over .env
        self.mode = mode
        self.stats = stats
        self.app: Optional[App] = None
        self._window = None
        self._preview = VoicePreview()
        overlay.on_close(self.stop)
        overlay.on_settings(self.open_settings)

    def start(self) -> None:
        self.stats.reset()
        self.app = App(self.settings, self.overlay, self.mode, on_dubbing=self._dubbing_available)
        self.app.start()
        self.overlay.configure_dub(False, False, self.toggle_dub)  # until the App has checked the device

    def _dubbing_available(self, available: bool) -> None:  # pipeline thread; configure_dub is queued
        self.overlay.configure_dub(available, available and self.settings.dub_incoming, self.toggle_dub)

    def toggle_dub(self, on: bool) -> None:  # Tk thread, from the right-click menu
        if not (self.app and self.app.set_dubbing(on)):
            self.overlay.set_status("Can't speak their translation: check its device in Settings…")
            self.overlay.configure_dub(bool(self.settings.dub_device), False, self.toggle_dub)
            return
        self.settings = dataclasses.replace(self.settings, dub_incoming=on)
        if "DUB_INCOMING" not in locked_keys(self.environ):  # remember it for next time
            try:
                write_env(self.env_file, {"DUB_INCOMING": "true" if on else "false"})
            except OSError as exc:
                log.warning("Could not save DUB_INCOMING: %s", exc)
        self.overlay.set_status("Speaking their translation to you" if on
                                else "Not speaking their translation (subtitles only)")

    def stop(self) -> None:
        if self.app:
            self.app.stop()

    def _run(self, job: Job[T]) -> concurrent.futures.Future[T]:
        future = self.app.submit(job) if self.app else None
        return future if future is not None else run_standalone(self.settings, job)

    def open_settings(self) -> None:
        from app.ui.settings_window import SettingsWindow  # only needed once opened

        if self._window is not None:
            self._window.win.deiconify()
            self._window.win.lift()
            return

        def closed() -> None:
            self._window = None

        def devices() -> concurrent.futures.Future[Devices]:
            return self._run(list_devices)

        def preview(lang: str, voice: str, style: Optional[str],
                    device: Optional[str]) -> concurrent.futures.Future[str]:
            return self._run(self._preview.job(lang, voice, style, device))

        def check_device(device: str) -> concurrent.futures.Future[float]:
            future = self._run(check_dub_device(device))
            # Remember the result, so the restart after saving doesn't test the device again.
            future.add_done_callback(lambda f: f.exception() is None and remember_check(device, f.result()))
            return future

        self._window = SettingsWindow(self.overlay.root, self.settings, locked_keys(self.environ), self.stats,
                                      devices, preview, self.apply, closed, check_device)

    def apply(self, updates: dict[str, str]) -> None:
        try:
            write_env(self.env_file, updates)
        except OSError as exc:
            log.error("Could not save settings to %s: %s", self.env_file, exc)
            self.overlay.set_status(f"Couldn't save settings: {exc}")
            return
        log.info("Settings saved to %s: %s", self.env_file,
                 ", ".join(f"{k}={v}" for k, v in sorted(updates.items())))  # managed keys only: no secrets
        self.overlay.set_status("Restarting with the new settings…", active=True)
        threading.Thread(target=self._restart, name="restart", daemon=True).start()

    def _restart(self) -> None:  # worker thread
        if self.app:
            self.app.stop(timeout=60)  # a local model may still be loading
        self.app = None
        _free_gpu()
        self.overlay.call_soon(self._start_with_new_settings)

    def _start_with_new_settings(self) -> None:  # Tk thread (also re-reads a DUB_INCOMING toggled since)
        try:
            self.settings = load_settings(self.env_file, self.environ)
        except ConfigError as exc:
            log.error("Settings error: %s", exc)
            self.overlay.set_status(f"Settings error: {exc} Right-click → Settings… to fix it.")
            return
        s = self.settings
        self.overlay.set_languages(s.my_language, s.their_language, s.caption_hold_s)
        self.start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Live Indic interpreter for Meet/Zoom, powered by Bodhan.AI")
    parser.add_argument("--mode", choices=["both", "subtitles", "interpret"], default="both")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--log-file", help="also write the log here (UTF-8), e.g. for tools.latency_report")
    args = parser.parse_args()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file, encoding="utf-8"))
    logging.basicConfig(level=args.log_level.upper(), handlers=handlers,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    stats = LatencyStats()
    logging.getLogger("app.pipeline").addHandler(stats)

    environ = dict(os.environ)  # before anything reads .env: the shell's own variables
    settings = load_settings(args.env, environ)
    overlay = Overlay(settings.my_language, settings.their_language, settings.caption_hold_s)
    controller = Controller(overlay, settings, args.env, environ, args.mode, stats)
    controller.start()
    overlay.run()
    log.info("Overlay closed; exiting")


if __name__ == "__main__":
    main()
