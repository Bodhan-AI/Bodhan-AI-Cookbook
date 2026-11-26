"""Always-on-top subtitle bar (tkinter, no extra dependencies).

Thread-safe: pipelines call show_caption/set_status from the asyncio thread;
updates are queued and applied on the Tk main thread.
Drag with the left mouse button; right-click for Settings… and Quit.

"Nirmala UI" ships with Windows 10/11 and covers all major Indic scripts.
If conjuncts render incorrectly on a machine, switch the UI to PySide6 (same thread-safe API).
"""
from __future__ import annotations

import logging
import queue
import time
import tkinter as tk
from typing import Callable, Optional

from app.pipeline.common import Caption

BG = "#16222E"
TEXT = "#F4F7FA"
MUTED = "#93A4B5"
THEM = "#5CC8B0"
YOU = "#E8B04B"
FONT = "Nirmala UI"

log = logging.getLogger(__name__)
PENDING = "…"
DUB_LABEL = "Speak their translation to me"
DUB_MENU_INDEX = 1  # Settings…, this, separator, Quit


def translated_line(caption: Caption, shown: Optional[Caption]) -> tuple[str, bool]:
    """Text for the big line, and whether it is still waiting for its translation.

    A live caption (the speaker is still talking) ends in "…". A pending caption that continues
    the one on screen, or finalises its live version, keeps that translation until its own
    arrives, so a sentence grows in place ("for now i …" → "for now I'll cut the call").
    """
    if caption.live:
        return f"{caption.translated} {PENDING}", False
    if not caption.pending:
        return caption.translated, False
    if (shown is not None and shown.direction == caption.direction and shown.translated
            and shown.seq in (caption.seq, caption.replaces)):
        return f"{shown.translated} {PENDING}", True
    return PENDING, True


class Overlay:
    def __init__(self, my_language: str, their_language: str, hold_s: float = 8.0) -> None:
        self._hold_s = hold_s
        self._labels: dict[str, str] = {}
        self._set_languages(my_language, their_language)
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._last_caption = 0.0
        self._shown: Optional[Caption] = None
        self._on_close: Optional[Callable[[], None]] = None
        self._on_settings: Optional[Callable[[], None]] = None
        self._closing = False

        self.root = tk.Tk()
        self.root.title("Live interpreter")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.9)
        self.root.configure(bg=BG)

        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        width, height = int(sw * 0.62), 150
        self.root.geometry(f"{width}x{height}+{(sw - width) // 2}+{sh - height - 90}")
        wrap = width - 48

        self._who = tk.Label(self.root, text="", font=(FONT, 10, "bold"), fg=THEM, bg=BG, anchor="w")
        self._original = tk.Label(self.root, text="", font=(FONT, 13), fg=MUTED, bg=BG,
                                  anchor="w", justify="left", wraplength=wrap)
        self._translated = tk.Label(self.root, text="", font=(FONT, 20, "bold"), fg=TEXT, bg=BG,
                                    anchor="w", justify="left", wraplength=wrap)
        self._status = tk.Label(self.root, text="Starting…", font=(FONT, 10), fg=MUTED, bg=BG, anchor="w")
        for widget in (self._who, self._original, self._translated):
            widget.pack(fill="x", padx=24)
        self._who.pack_configure(pady=(14, 0))
        self._status.pack(fill="x", padx=24, side="bottom", pady=(0, 10))

        # Bind on the toplevel only: its bindtag is on every child, so clicks on the labels
        # arrive here too. Binding the labels as well fired each handler twice.
        self.root.bind("<ButtonPress-1>", self._drag_start)
        self.root.bind("<B1-Motion>", self._drag_move)
        self.root.bind("<Button-3>", self._right_click)
        self._menu = tk.Menu(self.root, tearoff=False)
        self._menu.add_command(label="Settings…", command=self._open_settings)
        self._dub_on = tk.BooleanVar(value=False)
        self._on_dub: Optional[Callable[[bool], None]] = None
        self._menu.add_checkbutton(label=DUB_LABEL, variable=self._dub_on, command=self._toggle_dub,
                                   state="disabled")
        self._menu.add_separator()
        self._menu.add_command(label="Quit", command=self._quit)
        self.root.after(40, self._poll)

    # ---------------------------------------------------- thread-safe API

    def show_caption(self, caption: Caption) -> None:
        self._queue.put(lambda: self._apply_caption(caption))

    def set_status(self, text: str, active: bool = False) -> None:
        self._queue.put(lambda: self._status.configure(text=text, fg=YOU if active else MUTED))

    def on_close(self, callback: Callable[[], None]) -> None:
        self._on_close = callback

    def on_settings(self, callback: Callable[[], None]) -> None:
        """Called on the Tk thread when "Settings…" is chosen from the right-click menu."""
        self._on_settings = callback

    def configure_dub(self, available: bool, on: bool, toggle: Callable[[bool], None]) -> None:
        """The right-click "Speak their translation" switch. Unavailable until Settings sets a
        device for it; toggle(on) is called on the Tk thread when it's flipped."""
        def apply() -> None:
            self._on_dub = toggle
            self._dub_on.set(on and available)
            label = DUB_LABEL if available else f"{DUB_LABEL} (needs a safe device: see Settings)"
            self._menu.entryconfigure(DUB_MENU_INDEX, label=label, state="normal" if available else "disabled")
        self._queue.put(apply)

    def call_soon(self, fn: Callable[[], None]) -> None:
        """Run fn on the Tk thread (from any thread)."""
        self._queue.put(fn)

    def set_languages(self, my_language: str, their_language: str, hold_s: float) -> None:
        def apply() -> None:
            self._set_languages(my_language, their_language)
            self._hold_s = hold_s
        self._queue.put(apply)

    def close(self) -> None:
        self._queue.put(self._shutdown)

    def run(self) -> None:
        self.root.mainloop()

    # ---------------------------------------------------------- Tk thread

    def _set_languages(self, my_language: str, their_language: str) -> None:
        self._labels = {"in": f"Them ({their_language} → {my_language})",
                        "out": f"You ({my_language} → {their_language})"}

    def _apply_caption(self, caption: Caption) -> None:
        self._who.configure(text=self._labels[caption.direction], fg=THEM if caption.direction == "in" else YOU)
        self._original.configure(text=caption.original)
        text, waiting = translated_line(caption, self._shown)
        self._translated.configure(text=text, fg=MUTED if waiting else TEXT)
        if not waiting:
            self._shown = caption
        self._last_caption = time.monotonic()

    def _poll(self) -> None:
        while not self._closing:
            try:
                self._queue.get_nowait()()
            except queue.Empty:
                break
        if self._closing:  # _shutdown destroyed the window: drop late updates, stop polling
            return
        if self._last_caption and time.monotonic() - self._last_caption > self._hold_s:
            for widget in (self._who, self._original, self._translated):
                widget.configure(text="")
            self._last_caption = 0.0
            self._shown = None
        self.root.after(40, self._poll)

    def _right_click(self, event: tk.Event) -> None:
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()

    def _toggle_dub(self) -> None:
        if self._on_dub:
            self._on_dub(bool(self._dub_on.get()))

    def _open_settings(self) -> None:
        if self._on_settings:
            self._on_settings()

    def _quit(self) -> None:
        log.info("Quit chosen from the bar's menu")
        self.close()

    def _shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._on_close:
            self._on_close()
        self.root.destroy()

    def _drag_start(self, event: tk.Event) -> None:
        self._dx, self._dy = event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y()

    def _drag_move(self, event: tk.Event) -> None:
        self.root.geometry(f"+{event.x_root - self._dx}+{event.y_root - self._dy}")
