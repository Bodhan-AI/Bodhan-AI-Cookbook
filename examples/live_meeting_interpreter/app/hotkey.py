"""Global hold-to-talk key (works while Meet/Zoom has focus).

Note: Windows does not deliver key events to a non-elevated app while an
elevated (admin) window is focused. Meet in Chrome and Zoom are not elevated.
"""
from __future__ import annotations

from typing import Callable

from pynput import keyboard


def parse_key(name: str):
    name = name.strip().lower()
    if hasattr(keyboard.Key, name):
        return getattr(keyboard.Key, name)  # e.g. ctrl_r, alt_r, f9, scroll_lock
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    raise ValueError(f"Unknown PTT_KEY {name!r}; use a pynput Key name such as ctrl_r or f9")


_SIDES = {"_l": "Left ", "_r": "Right "}


def key_label(name: str) -> str:
    """pynput key name -> what's printed on the keyboard: ctrl_r -> "Right Ctrl", f9 -> "F9"."""
    name = name.strip().lower()
    side = ""
    if name[-2:] in _SIDES and len(name) > 2:
        side, name = _SIDES[name[-2:]], name[:-2]
    words = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Win", "alt_gr": "AltGr"}
    label = words.get(name) or (name.upper() if len(name) <= 3 else name.replace("_", " ").title())
    return side + label


class PushToTalk:
    def __init__(self, key_name: str, on_press: Callable[[], None], on_release: Callable[[], None]) -> None:
        self._key = parse_key(key_name)
        self._on_press = on_press
        self._on_release = on_release
        self._down = False
        self._listener = keyboard.Listener(on_press=self._press, on_release=self._release, daemon=True)

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        self._listener.stop()

    def _press(self, key) -> None:
        if key == self._key and not self._down:  # ignore auto-repeat
            self._down = True
            self._on_press()

    def _release(self, key) -> None:
        if key == self._key and self._down:
            self._down = False
            self._on_release()
