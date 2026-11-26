"""Settings window (tkinter, opened from the bar's right-click menu).

Languages, voice (with a spoken preview), push-to-talk key, devices, speech-recognition engine
and a live latency readout. "Save and restart" writes the managed lines of .env and restarts
the pipelines; nothing else in .env changes.

Runs on the Tk thread and never blocks it: device lists and previews are futures, polled with
after().
"""
from __future__ import annotations

import concurrent.futures
import logging
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from app.audio.capture import is_virtual_cable
from app.audio.leak import LEAK_GAIN
from app.bodhan.languages import default_voice, language_name
from app.config import Settings
from app.hotkey import key_label
from app.services import Devices
from app.settings_store import (
    Form, changed, is_typing_key, my_language_codes, style_choices, their_language_codes, tk_key_to_ptt,
    validate, voice_choices,
)
from app.stats import LatencyStats

log = logging.getLogger(__name__)
FONT = ("Segoe UI", 10)
MUTED = "#5f6b76"
ERROR = "#b3261e"
SCRIPT_LABELS = {"native": "Native script", "roman": "Roman letters", "codemix": "Mixed (code-mix)"}
NEUTRAL = "Neutral"
NO_DEVICE = "(none)"


def _lang_label(code: str) -> str:
    return f"{language_name(code)} ({code})"


class SettingsWindow:
    def __init__(self, parent: tk.Misc, settings: Settings, locked: list[str], stats: LatencyStats,
                 devices: Callable[[], "concurrent.futures.Future[Devices]"],
                 preview: Callable[[str, str, Optional[str], Optional[str]], "concurrent.futures.Future[str]"],
                 on_save: Callable[[dict[str, str]], None], on_close: Callable[[], None],
                 check_device: Callable[[str], "concurrent.futures.Future[float]"]) -> None:
        self.s = settings
        self._check_device = check_device
        self._locked = set(locked)
        self._stats = stats
        self._preview = preview
        self._on_save = on_save
        self._on_close = on_close
        self._ptt = settings.ptt_key
        self._capturing = False
        self._closed = False
        self._timers: set[str] = set()  # pending after() ids, cancelled on close
        self._captured = ""  # Windows' default output, once the device list is in

        self.win = tk.Toplevel(parent)
        self.win.title("Live interpreter settings")
        self.win.attributes("-topmost", True)
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.win.bind("<KeyPress>", self._key)
        style = ttk.Style(self.win)
        style.configure(".", font=FONT)
        body = ttk.Frame(self.win, padding=16)
        body.grid(sticky="nsew")
        self._build(body)
        self._load_devices(devices())
        self._refresh_readout()
        self.win.update_idletasks()
        # Above the subtitle bar (also always on top), which would otherwise cover the buttons.
        x = (self.win.winfo_screenwidth() - self.win.winfo_reqwidth()) // 2
        y = (self.win.winfo_screenheight() - self.win.winfo_reqheight()) // 3
        bar_top = parent.winfo_rooty() if parent.winfo_viewable() else self.win.winfo_screenheight()
        y = max(8, min(y, bar_top - self.win.winfo_reqheight() - 40))
        self.win.geometry(f"+{x}+{y}")
        self.win.focus_force()

    # ------------------------------------------------------------------- layout

    def _section(self, parent: ttk.Frame, title: str, row: int) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text=title, padding=(12, 8))
        frame.grid(row=row, column=0, sticky="nsew", pady=(0, 10))
        frame.columnconfigure(1, weight=1)
        return frame

    def _row(self, frame: ttk.Frame, row: int, label: str, widget: tk.Widget) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=3)
        widget.grid(row=row, column=1, sticky="ew", pady=3)

    def _combo(self, frame: ttk.Frame, values: list[str], current: str, width: int = 44) -> ttk.Combobox:
        box = ttk.Combobox(frame, values=values, state="readonly", width=width)
        box.set(current)
        return box

    def _build(self, body: ttk.Frame) -> None:
        # Two columns plus a full-width strip: a laptop at 125% scaling is only ~864 units tall,
        # and the window must end above the always-on-top subtitle bar (~624).
        s = self.s
        if self._locked:
            ttk.Label(body, foreground=MUTED, wraplength=900, justify="left",
                      text=f"Set in the window that started the app, so not changeable here: "
                           f"{', '.join(sorted(self._locked))}.").grid(row=0, column=0, columnspan=2,
                                                                      sticky="w", pady=(0, 8))
        left, right = ttk.Frame(body), ttk.Frame(body)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        right.grid(row=1, column=1, sticky="nsew")
        for col in (left, right):
            col.columnconfigure(0, weight=1)

        # Languages
        row = 0
        lang = self._section(left, "Languages", row)
        row += 1
        self._mine_codes, self._their_codes = my_language_codes(), their_language_codes()
        self.mine = self._combo(lang, [_lang_label(c) for c in self._mine_codes], _lang_label(s.my_language))
        self.theirs = self._combo(lang, [_lang_label(c) for c in self._their_codes], _lang_label(s.their_language))
        self.script = self._combo(lang, list(SCRIPT_LABELS.values()), SCRIPT_LABELS[s.subtitle_script])
        self._row(lang, 0, "You speak", self.mine)
        self._row(lang, 1, "They speak", self.theirs)
        self._row(lang, 2, "Subtitle script", self.script)
        self.mine.bind("<<ComboboxSelected>>", lambda _e: self._sync())
        self.theirs.bind("<<ComboboxSelected>>", lambda _e: self._sync())

        # Interpreting: your voice, in their language
        voice = self._section(left, "Interpreting (what they hear)", row)
        row += 1
        self._voices = voice_choices()
        auto_voice = self._auto_voice_label()
        current_voice = next((label for v, label in self._voices if v == s.tts_voice), auto_voice)
        if s.tts_voice == default_voice(s.their_language):
            current_voice = auto_voice
        self.voice = self._combo(voice, [auto_voice] + [label for _, label in self._voices], current_voice)
        self.style = self._combo(voice, [NEUTRAL] + style_choices(), s.tts_style or NEUTRAL)
        self._row(voice, 0, "Voice", self.voice)
        self._row(voice, 1, "Style", self.style)
        preview_row = ttk.Frame(voice)
        self.preview_btn = ttk.Button(preview_row, text="Preview", command=self._start_preview)
        self.preview_btn.pack(side="left")
        self.preview_note = ttk.Label(preview_row, foreground=MUTED, text="Plays a sample line on your speakers.")
        self.preview_note.pack(side="left", padx=10)
        preview_row.grid(row=2, column=1, sticky="w", pady=(4, 0))
        ptt_row = ttk.Frame(voice)
        self.ptt_label = ttk.Label(ptt_row, text=key_label(self._ptt), width=16)
        self.ptt_label.pack(side="left")
        self.ptt_btn = ttk.Button(ptt_row, text="Change…", command=self._capture_key)
        self.ptt_btn.pack(side="left", padx=(8, 0))
        self._row(voice, 3, "Hold to talk", ptt_row)
        self.ptt_note = ttk.Label(voice, foreground=MUTED, text="")
        self.ptt_note.grid(row=4, column=1, sticky="w")

        # Dubbing: their side, spoken to you (optional); right column, under Devices
        dub = self._section(right, "Their side, spoken to you (optional)", 1)
        self.dub_on = tk.BooleanVar(value=s.dub_incoming)
        self.dub_box = ttk.Checkbutton(dub, text="Speak their translation to me", variable=self.dub_on)
        self.dub_box.grid(row=0, column=1, sticky="w", pady=3)
        self.dub_device = self._combo(dub, ["Loading…"], "Loading…")
        self.dub_device.configure(state="disabled")
        self.dub_device.bind("<<ComboboxSelected>>", lambda _e: self._sync())
        self._row(dub, 1, "Play on", self.dub_device)
        current_dub_voice = next((label for v, label in self._voices if v == s.dub_voice), "")
        if not current_dub_voice or s.dub_voice == default_voice(s.my_language):
            current_dub_voice = self._auto_dub_voice_label(s.my_language)
        self.dub_voice = self._combo(dub, [current_dub_voice] + [label for _, label in self._voices],
                                     current_dub_voice)
        self._row(dub, 2, "Voice", self.dub_voice)
        self.dub_preview_btn = ttk.Button(dub, text="Preview", command=self._start_dub_preview)
        self.dub_preview_btn.grid(row=0, column=1, sticky="e", pady=3)
        self.dub_note = ttk.Label(dub, foreground=MUTED, wraplength=440, justify="left",
                                  text="Needs a device subtitles can't hear, e.g. USB or Bluetooth headphones. "
                                       "Save tests it with a short voice clip.")
        self.dub_note.grid(row=3, column=0, columnspan=2, sticky="w", pady=(2, 0))

        # Devices
        dev = self._section(right, "Devices", 0)
        self.mic = self._combo(dev, ["Loading…"], "Loading…")
        self.cable = self._combo(dev, ["Loading…"], "Loading…")
        self.mic.configure(state="disabled")
        self.cable.configure(state="disabled")
        self.monitor = tk.BooleanVar(value=s.monitor_tts)
        self._row(dev, 0, "Microphone", self.mic)
        self._row(dev, 1, "Meeting mic (VB-Cable)", self.cable)
        self.monitor_box = ttk.Checkbutton(dev, text="Also play my translated voice on my speakers",
                                           variable=self.monitor)
        self.monitor_box.grid(row=2, column=1, sticky="w", pady=3)
        self.dev_note = ttk.Label(dev, foreground=MUTED, wraplength=440, justify="left",
                                  text="Subtitles listen to Windows' default output.")
        self.dev_note.grid(row=3, column=0, columnspan=2, sticky="w")

        # Engine: full width under both columns, one row of controls, readout wide and short
        eng = ttk.LabelFrame(body, text="Speech recognition", padding=(12, 8))
        eng.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.backend = tk.StringVar(value=s.asr_backend)
        radios = ttk.Frame(eng)
        ttk.Label(radios, text="Runs on").pack(side="left", padx=(0, 12))
        self.hosted_radio = ttk.Radiobutton(radios, text="Bodhan cloud (fastest)", value="hosted",
                                            variable=self.backend, command=self._sync)
        self.local_radio = ttk.Radiobutton(radios, text="This PC's GPU (free, offline)", value="local",
                                           variable=self.backend, command=self._sync)
        self.hosted_radio.pack(side="left")
        self.local_radio.pack(side="left", padx=(12, 24))
        self.live = tk.BooleanVar(value=s.live_caption_interval_s > 0)
        self.live_box = ttk.Checkbutton(radios, text="Live captions while they speak (GPU only)",
                                        variable=self.live)
        self.live_box.pack(side="left")
        radios.grid(row=0, column=0, sticky="w")
        self.readout = ttk.Label(eng, foreground=MUTED, justify="left", wraplength=1000, text="")
        self.readout.grid(row=1, column=0, sticky="w", pady=(6, 0))

        # Problems on the left of the buttons, in one row
        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        buttons.columnconfigure(0, weight=1)
        self.problems = ttk.Label(buttons, foreground=ERROR, wraplength=760, justify="left", text="")
        self.problems.grid(row=0, column=0, sticky="w")
        self.save_btn = ttk.Button(buttons, text="Save and restart", command=self._save)
        self.save_btn.grid(row=0, column=1, sticky="e", padx=(8, 8))
        ttk.Button(buttons, text="Close", command=self.close).grid(row=0, column=2, sticky="e")

        for key, widgets in {
            "MY_LANGUAGE": [self.mine], "THEIR_LANGUAGE": [self.theirs], "SUBTITLE_SCRIPT": [self.script],
            "TTS_VOICE": [self.voice], "TTS_STYLE": [self.style], "PTT_KEY": [self.ptt_btn],
            "MONITOR_TTS": [self.monitor_box], "ASR_BACKEND": [self.hosted_radio, self.local_radio],
            "LIVE_CAPTION_INTERVAL_S": [self.live_box], "DUB_INCOMING": [self.dub_box],
            "DUB_VOICE": [self.dub_voice],
        }.items():
            if key in self._locked:
                for w in widgets:
                    w.configure(state="disabled")
        self._sync()

    # ------------------------------------------------------------- behaviour

    def _later(self, ms: int, fn: Callable[[], None]) -> None:
        """after(), but cancelled when the window closes (else Tk errors once it is gone)."""
        timer = ""

        def run() -> None:
            self._timers.discard(timer)
            if not self._closed:
                fn()

        timer = self.win.after(ms, run)
        self._timers.add(timer)

    def _code(self, box: ttk.Combobox, codes: list[str], fallback: str) -> str:
        labels = [_lang_label(c) for c in codes]
        return codes[labels.index(box.get())] if box.get() in labels else fallback

    def _auto_voice_label(self, lang: Optional[str] = None) -> str:
        lang = lang or self.s.their_language
        return f"Automatic ({default_voice(lang)}, recorded in {language_name(lang)})"

    def _auto_dub_voice_label(self, lang: str) -> str:
        return f"Automatic ({default_voice(lang)})"

    def _dub_device_choice(self) -> str:
        choice = self.dub_device.get()
        return "" if choice in ("Loading…", NO_DEVICE) else choice

    def _sync(self) -> None:
        """Keep dependent widgets consistent with the current choices."""
        mine = self._code(self.mine, self._mine_codes, self.s.my_language)
        theirs = self._code(self.theirs, self._their_codes, self.s.their_language)
        dub_values = list(self.dub_voice.cget("values"))
        if dub_values and dub_values[0].startswith("Automatic"):
            was_auto = self.dub_voice.get() == dub_values[0]
            dub_values[0] = self._auto_dub_voice_label(mine)
            self.dub_voice.configure(values=dub_values)
            if was_auto:
                self.dub_voice.set(dub_values[0])
        if "DUB_INCOMING" not in self._locked:
            # Only once a safe device is chosen: never the output subtitles listen to.
            loading = self.dub_device.get() == "Loading…"
            has_device = bool(self._dub_device_choice())
            self.dub_box.configure(state="normal" if has_device else "disabled")
            if not has_device and not loading:
                self.dub_on.set(False)
        if "SUBTITLE_SCRIPT" not in self._locked:
            # Roman/code-mix only change Indic subtitles; for English they do nothing.
            self.script.configure(state="disabled" if mine == "en" else "readonly")
        values = list(self.voice.cget("values"))
        if values and values[0].startswith("Automatic"):
            was_auto = self.voice.get() == values[0]
            values[0] = self._auto_voice_label(theirs)
            self.voice.configure(values=values)
            if was_auto:
                self.voice.set(values[0])
        if "LIVE_CAPTION_INTERVAL_S" not in self._locked:
            self.live_box.configure(state="normal" if self.backend.get() == "local" else "disabled")

    def _load_devices(self, future: "concurrent.futures.Future[Devices]") -> None:
        if self._closed:
            return
        if not future.done():
            self._later(150, lambda: self._load_devices(future))
            return
        try:
            d = future.result()
        except Exception as exc:
            log.warning("Could not list audio devices: %s", exc)
            self.dev_note.configure(text=f"Couldn't list devices ({exc}). The current ones are kept.",
                                    foreground=ERROR)
            self._fill_devices([], [], self.s.mic_device or "", self.s.cable_device)
            self._fill_dub([])
            return
        mics = [m for m in d.microphones if not is_virtual_cable(m)]
        cables = [o for o in d.outputs if is_virtual_cable(o)]
        self._captured = d.default_output
        self._fill_devices(mics, cables, d.default_mic, self.s.cable_device)
        self._fill_dub([o for o in d.outputs if not is_virtual_cable(o) and o != d.default_output])
        if not cables:
            self.dev_note.configure(text="VB-Cable isn't installed, so the meeting can't hear you. See README.",
                                    foreground=ERROR)
        elif is_virtual_cable(d.default_mic) and not self.s.mic_device:
            self.dev_note.configure(text="Windows' default microphone is VB-Cable, which turns interpreting "
                                         "off. Choose your real microphone above.", foreground=ERROR)

    def _fill_devices(self, mics: list[str], cables: list[str], default_mic: str, cable: str) -> None:
        default_label = f"Windows default ({default_mic})" if default_mic else "Windows default"
        current_mic = next((m for m in mics if self.s.mic_device and self.s.mic_device.lower() in m.lower()),
                           self.s.mic_device or default_label)
        self.mic.configure(values=[default_label] + mics, state="readonly")
        self.mic.set(current_mic)
        current_cable = next((c for c in cables if cable.lower() in c.lower()), cable)
        self.cable.configure(values=cables or [cable], state="readonly")
        self.cable.set(current_cable)
        for key, box in (("MIC_DEVICE", self.mic), ("CABLE_DEVICE", self.cable)):
            if key in self._locked:
                box.configure(state="disabled")

    def _fill_dub(self, outputs: list[str]) -> None:
        """Outputs their spoken translation may use: not VB-Cable, not where subtitles listen."""
        configured = self.s.dub_device or ""
        current = next((o for o in outputs if configured and configured.lower() in o.lower()), configured)
        values = [NO_DEVICE] + outputs + ([current] if current and current not in outputs else [])
        self.dub_device.configure(values=values, state="disabled" if "DUB_DEVICE" in self._locked else "readonly")
        self.dub_device.set(current or NO_DEVICE)
        if not outputs:
            self.dub_note.configure(text="Your only output is where subtitles listen. Plug in headphones "
                                         "(or set the meeting to play elsewhere) to use this.", foreground=MUTED)
        self._sync()

    def _voice_code(self, box: Optional[ttk.Combobox] = None) -> str:
        """"" for automatic, else the voice name."""
        choice = (box or self.voice).get()
        return next((v for v, label in self._voices if label == choice), "")

    def _start_preview(self) -> None:
        theirs = self._code(self.theirs, self._their_codes, self.s.their_language)
        voice = self._voice_code() or default_voice(theirs)
        style = None if self.style.get() == NEUTRAL else self.style.get()
        self._run_preview(self.preview_btn, self.preview_note, theirs, voice, style, None)

    def _start_dub_preview(self) -> None:
        mine = self._code(self.mine, self._mine_codes, self.s.my_language)
        voice = self._voice_code(self.dub_voice) or default_voice(mine)
        self._run_preview(self.dub_preview_btn, self.dub_note, mine, voice, None, self._dub_device_choice() or None)

    def _run_preview(self, button: ttk.Button, note: ttk.Label, lang: str, voice: str, style: Optional[str],
                     device: Optional[str]) -> None:
        button.configure(state="disabled")
        note.configure(text=f"Speaking as {voice}…", foreground=MUTED)
        self._wait_preview(self._preview(lang, voice, style, device), button, note)

    def _wait_preview(self, future: "concurrent.futures.Future[str]", button: ttk.Button, note: ttk.Label) -> None:
        if self._closed:
            return
        if not future.done():
            self._later(150, lambda: self._wait_preview(future, button, note))
            return
        button.configure(state="normal")
        try:
            line = future.result()
            note.configure(text=f"“{line[:48]}{'…' if len(line) > 48 else ''}”", foreground=MUTED)
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            note.configure(text=f"Preview failed ({code}). Try again in a minute.", foreground=ERROR)

    def _capture_key(self) -> None:
        self._capturing = True
        self.ptt_label.configure(text="Press the key…")
        self.ptt_note.configure(text="Esc cancels.", foreground=MUTED)
        self.win.focus_force()

    def _key(self, event: tk.Event) -> None:
        if not self._capturing:
            return
        self._capturing = False
        if event.keysym == "Escape":
            self.ptt_label.configure(text=key_label(self._ptt))
            self.ptt_note.configure(text="")
            return
        name = tk_key_to_ptt(event.keysym, event.char or "")
        if name is None:
            self.ptt_label.configure(text=key_label(self._ptt))
            self.ptt_note.configure(text=f"{event.keysym} can't be used. Try Right Ctrl, Right Alt or F9.",
                                    foreground=ERROR)
            return
        self._ptt = name
        self.ptt_label.configure(text=key_label(name))
        self.ptt_note.configure(
            text="Careful: this key also triggers push-to-talk while you type." if is_typing_key(name) else "",
            foreground=ERROR if is_typing_key(name) else MUTED)

    def _refresh_readout(self) -> None:
        if self._closed:
            return
        engine = "Bodhan cloud" if self.s.asr_backend == "hosted" else "this PC's GPU"
        lines = [f"Now running on {engine}. Medians of the last sentences:"]
        snap = self._stats.snapshot()
        n_in, st_in = snap.get("in", (0, {}))
        n_out, st_out = snap.get("out", (0, {}))
        if n_in:
            lines.append(f"Subtitles ({n_in}): recognition {st_in.get('asr', 0):.2f} s, translation "
                         f"{st_in.get('mt', 0):.2f} s, shown {st_in.get('e2e', 0):.2f} s after they pause.")
        if n_out:
            lines.append(f"Interpreting ({n_out}): recognition {st_out.get('asr', 0):.2f} s, translation "
                         f"{st_out.get('mt', 0):.2f} s, voice {st_out.get('tts', 0):.2f} s, heard "
                         f"{st_out.get('e2e', 0):.2f} s after you let go.")
        if not (n_in or n_out):
            lines.append("No sentences yet.")
        self.readout.configure(text="\n".join(lines))
        self._later(1000, self._refresh_readout)

    def form(self) -> Form:
        s = self.s
        script = next((k for k, v in SCRIPT_LABELS.items() if v == self.script.get()), s.subtitle_script)
        mic = self.mic.get()
        mic_device = "" if mic.startswith("Windows default") or mic == "Loading…" else mic
        cable = self.cable.get()
        return Form(
            my_language=self._code(self.mine, self._mine_codes, s.my_language),
            their_language=self._code(self.theirs, self._their_codes, s.their_language),
            subtitle_script=script,
            voice=self._voice_code(),
            style="" if self.style.get() == NEUTRAL else self.style.get(),
            ptt_key=self._ptt,
            mic_device=mic_device if mic != "Loading…" else (s.mic_device or ""),
            cable_device=s.cable_device if cable == "Loading…" else cable,
            monitor_tts=bool(self.monitor.get()),
            asr_backend=self.backend.get(),
            live_captions=bool(self.live.get()),
            local_model_dir=s.local_asr_model,
            dub_incoming=bool(self.dub_on.get()),
            dub_device=self._dub_device_choice() if self.dub_device.get() != "Loading…" else (s.dub_device or ""),
            dub_voice=self._voice_code(self.dub_voice),
            captured_output=self._captured,
        )

    def _save(self) -> None:
        form = self.form()
        updates, problems = validate(form)
        if problems:
            self.problems.configure(text="\n".join(problems), foreground=ERROR)
            return
        for key in self._locked:  # the shell's value wins anyway: don't make it permanent in .env
            updates.pop(key, None)
        updates = changed(updates, self.s)
        if not updates:
            self.problems.configure(text="Nothing changed.", foreground=MUTED)
            return
        if form.dub_incoming and ("DUB_INCOMING" in updates or "DUB_DEVICE" in updates):
            # A device can be "separate" yet mixed back into the speakers: test it for real.
            self.save_btn.configure(state="disabled")
            self.problems.configure(text=f"Checking that subtitles can't hear {form.dub_device} "
                                         "(plays a short voice clip on it)…", foreground=MUTED)
            self._wait_check(self._check_device(form.dub_device), form.dub_device, updates)
            return
        self._finish_save(updates)

    def _wait_check(self, future: "concurrent.futures.Future[float]", device: str, updates: dict[str, str]) -> None:
        if self._closed:
            return
        if not future.done():
            self._later(150, lambda: self._wait_check(future, device, updates))
            return
        self.save_btn.configure(state="normal")
        try:
            level = future.result()
        except Exception as exc:
            self.problems.configure(text=f"Couldn't check {device} ({exc}). Not saved.", foreground=ERROR)
            return
        log.info("Dub device check: %r leaks into the subtitles' input at gain %.3f (limit %.2f)", device, level, LEAK_GAIN)
        if level > LEAK_GAIN:
            self.problems.configure(
                text=f"Subtitles can hear {device}: it plays through the same speakers, so the app would "
                     "transcribe its own voice. Use headphones with their own connection (USB or Bluetooth).",
                foreground=ERROR)
            return
        self._finish_save(updates)

    def _finish_save(self, updates: dict[str, str]) -> None:
        self.problems.configure(text="", foreground=ERROR)
        self._on_save(updates)
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for timer in self._timers:
            try:
                self.win.after_cancel(timer)
            except tk.TclError:
                pass
        self._timers.clear()
        self.win.destroy()
        self._on_close()
