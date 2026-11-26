"""What the settings window may change, and how it writes .env.

Pure logic (no Tk), so it is unit-tested. The window edits only MANAGED_KEYS; API keys and
everything else in .env are left byte-for-byte as they were, comments included.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, Optional

from app.bodhan.languages import (
    ASR_LANGUAGES, TRANSLATE_LANGUAGES, TTS_LANGUAGES, TTS_STYLES, VOICES, language_name,
)

if TYPE_CHECKING:
    from app.config import Settings

MANAGED_KEYS = (
    "MY_LANGUAGE", "THEIR_LANGUAGE", "SUBTITLE_SCRIPT", "TTS_VOICE", "TTS_STYLE", "PTT_KEY",
    "MIC_DEVICE", "CABLE_DEVICE", "MONITOR_TTS", "ASR_BACKEND", "LIVE_CAPTION_INTERVAL_S",
    "DUB_INCOMING", "DUB_DEVICE", "DUB_VOICE",
)
SCRIPTS = ("native", "roman", "codemix")
LIVE_INTERVAL_ON = "2"

# ------------------------------------------------------------------------- .env file

_ASSIGNMENT = re.compile(r"^(?P<lead>\s*)(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=(?P<rest>.*?)(?P<eol>\r?\n)?$")
_INLINE_COMMENT = re.compile(r"\s+#.*$")


def _format_value(value: str) -> str:
    if value == "" or re.fullmatch(r"[\w.:/+-]+", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'  # spaces, brackets...


def update_env_text(text: str, updates: Mapping[str, str]) -> str:
    """Set KEY=value for each update: in place (keeping any inline comment) or appended."""
    lines = text.splitlines(keepends=True)
    done: set[str] = set()
    for i, line in enumerate(lines):
        m = _ASSIGNMENT.match(line)
        if not m or m.group("key") not in updates:
            continue
        key, rest = m.group("key"), m.group("rest")
        comment = ""
        if not rest.lstrip().startswith(('"', "'")):  # a quoted value may contain " #"
            c = _INLINE_COMMENT.search(rest)
            comment = c.group(0) if c else ""
        lines[i] = f"{m.group('lead')}{key}={_format_value(updates[key])}{comment}{m.group('eol') or ''}"
        done.add(key)
    missing = [k for k in updates if k not in done]
    if missing:
        newline = "\r\n" if "\r\n" in text else "\n"
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        lines += [f"{k}={_format_value(updates[k])}{newline}" for k in missing]
    return "".join(lines)


def write_env(path: str | Path, updates: Mapping[str, str]) -> None:
    """Rewrite .env atomically: a crash mid-write can't lose the API keys in it."""
    path = Path(path)
    unknown = set(updates) - set(MANAGED_KEYS)
    if unknown:
        raise ValueError(f"Not a setting the window manages: {sorted(unknown)}")
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(update_env_text(text, updates))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ------------------------------------------------------------------------ choices

def my_language_codes() -> list[str]:
    """Languages you can speak: transcribed and translated."""
    return sorted(ASR_LANGUAGES & TRANSLATE_LANGUAGES, key=language_name)


def their_language_codes() -> list[str]:
    """Languages the meeting can speak: transcribed, translated, and spoken back to them."""
    return sorted(ASR_LANGUAGES & TRANSLATE_LANGUAGES & TTS_LANGUAGES, key=language_name)


def voice_choices() -> list[tuple[str, str]]:
    """(voice, label) for every Bodhan voice, grouped by the language it was recorded in."""
    return [(v, f"{v} ({language_name(lang)})")
            for lang in sorted(VOICES, key=language_name) for v in VOICES[lang]]


def style_choices() -> list[str]:
    return sorted(TTS_STYLES)


# Tk keysym -> pynput key name (what PTT_KEY holds). Letters work too but fire while you type.
_TK_KEYS = {
    "Control_L": "ctrl_l", "Control_R": "ctrl_r", "Alt_L": "alt_l", "Alt_R": "alt_r",
    "Shift_L": "shift_l", "Shift_R": "shift_r", "Caps_Lock": "caps_lock", "Scroll_Lock": "scroll_lock",
    "Pause": "pause", "Insert": "insert", "Num_Lock": "num_lock", "App": "menu", "Menu": "menu",
    "Home": "home", "End": "end", "Prior": "page_up", "Next": "page_down",
}


def tk_key_to_ptt(keysym: str, char: str = "") -> Optional[str]:
    """The PTT_KEY name for a key pressed in a Tk window, or None if pynput can't watch it."""
    from app.hotkey import parse_key

    name = _TK_KEYS.get(keysym)
    if name is None and re.fullmatch(r"F([1-9]|1[0-9]|2[0-4])", keysym):
        name = keysym.lower()
    if name is None and len(char) == 1 and char.isprintable() and not char.isspace():
        name = char.lower()
    if name is None:
        return None
    try:
        parse_key(name)
    except ValueError:
        return None
    return name


def is_typing_key(ptt: str) -> bool:
    """A letter, digit or symbol: it would also trigger push-to-talk while you type in the chat."""
    return len(ptt) == 1


# ---------------------------------------------------------------------- the form

@dataclass
class Form:
    my_language: str
    their_language: str
    subtitle_script: str
    voice: str              # "" = automatic (a voice recorded in their language)
    style: str              # "" = neutral
    ptt_key: str
    mic_device: str         # "" = Windows default
    cable_device: str
    monitor_tts: bool
    asr_backend: str        # hosted | local
    live_captions: bool
    local_model_dir: str    # to check the weights are there before choosing local
    dub_incoming: bool = False
    dub_device: str = ""    # "" = none chosen
    dub_voice: str = ""     # "" = automatic (a voice for my_language)
    captured_output: str = ""  # Windows' default output: subtitles listen to it, so never dub there


def validate(form: Form) -> tuple[dict[str, str], list[str]]:
    """(.env updates, problems). Save only when there are no problems."""
    from app.audio.capture import is_virtual_cable
    from app.bodhan.languages import ALL_VOICES
    from app.hotkey import parse_key

    problems = []
    if form.my_language not in my_language_codes():
        problems.append("Choose the language you speak.")
    if form.their_language not in their_language_codes():
        problems.append("Choose the language the meeting speaks.")
    if form.my_language == form.their_language:
        problems.append("Choose two different languages.")
    if form.subtitle_script not in SCRIPTS:
        problems.append("Choose a subtitle script.")
    if form.voice and form.voice not in ALL_VOICES:
        problems.append("Choose a voice from the list.")
    if form.style and form.style not in TTS_STYLES:
        problems.append("Choose a voice style from the list.")
    try:
        parse_key(form.ptt_key)
    except ValueError:
        problems.append("Press Change and choose a push-to-talk key.")
    if form.mic_device and is_virtual_cable(form.mic_device):
        problems.append("The microphone can't be VB-Cable: interpreting would translate its own voice.")
    if not form.cable_device:
        problems.append("Choose the VB-Cable input the meeting's microphone listens to.")
    if form.asr_backend not in ("hosted", "local"):
        problems.append("Choose where speech recognition runs.")
    if form.asr_backend == "local" and not (Path(form.local_model_dir) / "model.safetensors").is_file():
        problems.append("Local speech recognition needs the model downloaded first (see README).")
    if form.dub_voice and form.dub_voice not in ALL_VOICES:
        problems.append("Choose a voice for their translation from the list.")
    if form.dub_incoming:
        problems += dub_device_problems(form.dub_device, form.cable_device, form.captured_output)
        if form.my_language not in TTS_LANGUAGES:
            problems.append(f"{language_name(form.my_language)} has no Bodhan voice, so their side can't be "
                            "spoken to you.")
    updates = {
        "MY_LANGUAGE": form.my_language,
        "THEIR_LANGUAGE": form.their_language,
        "SUBTITLE_SCRIPT": form.subtitle_script,
        "TTS_VOICE": form.voice,
        "TTS_STYLE": form.style,
        "PTT_KEY": form.ptt_key,
        "MIC_DEVICE": form.mic_device,
        "CABLE_DEVICE": form.cable_device,
        "MONITOR_TTS": "true" if form.monitor_tts else "false",
        "ASR_BACKEND": form.asr_backend,
        "LIVE_CAPTION_INTERVAL_S": LIVE_INTERVAL_ON if form.live_captions else "0",
        "DUB_INCOMING": "true" if form.dub_incoming else "false",
        "DUB_DEVICE": form.dub_device,
        "DUB_VOICE": form.dub_voice,
    }
    return updates, problems


def dub_device_problems(dub_device: str, cable_device: str, captured_output: str) -> list[str]:
    """Why a device can't take their spoken translation (empty if it can)."""
    from app.audio.capture import is_virtual_cable

    name = dub_device.lower()
    if not name:
        return ["Choose where to play their translation, e.g. your headphones."]
    if captured_output and (name in captured_output.lower() or captured_output.lower() in name):
        return ["Their translation can't play on Windows' default output: subtitles listen there and "
                "would transcribe it. Choose another device, e.g. headphones."]
    if is_virtual_cable(dub_device):
        # Every endpoint of VB-Cable ("CABLE Input", "CABLE In 16ch") feeds the meeting's mic.
        return ["Their translation can't play into VB-Cable: the meeting would hear it."]
    return []


def current_values(settings: "Settings") -> dict[str, str]:
    """The running settings in the same form as validate()'s updates."""
    from app.bodhan.languages import default_voice

    s = settings
    return {
        "MY_LANGUAGE": s.my_language,
        "THEIR_LANGUAGE": s.their_language,
        "SUBTITLE_SCRIPT": s.subtitle_script,
        "TTS_VOICE": "" if s.tts_voice == default_voice(s.their_language) else s.tts_voice,
        "TTS_STYLE": s.tts_style or "",
        "PTT_KEY": s.ptt_key,
        "MIC_DEVICE": s.mic_device or "",
        "CABLE_DEVICE": s.cable_device,
        "MONITOR_TTS": "true" if s.monitor_tts else "false",
        "ASR_BACKEND": s.asr_backend,
        "LIVE_CAPTION_INTERVAL_S": LIVE_INTERVAL_ON if s.live_caption_interval_s > 0 else "0",
        "DUB_INCOMING": "true" if s.dub_incoming else "false",
        "DUB_DEVICE": s.dub_device or "",
        "DUB_VOICE": "" if s.dub_voice == default_voice(s.my_language) else s.dub_voice,
    }


def changed(updates: Mapping[str, str], settings: "Settings") -> dict[str, str]:
    """Only the updates that differ from what is running, so .env gains no noise lines.

    Device settings match by substring (that's how the app finds devices), so picking the full
    name of the device already in use ("CABLE Input (VB-Audio Virtual Cable)" for "CABLE Input")
    is no change. A voice equal to the automatic one is no change either.
    """
    now = current_values(settings)
    out = {}
    for key, value in updates.items():
        old = now.get(key, "")
        if key in ("MIC_DEVICE", "CABLE_DEVICE", "DUB_DEVICE") and old and value and old.lower() in value.lower():
            continue
        if key == "LIVE_CAPTION_INTERVAL_S" and (value != "0") == (old != "0"):
            continue  # on stays on, whatever interval .env sets
        if value != old:
            out[key] = value
    return out


def locked_keys(environ: Mapping[str, str]) -> list[str]:
    """Managed settings set in the shell that started the app: they win over .env, so the
    window shows them read-only instead of saving a change that would do nothing."""
    return [k for k in MANAGED_KEYS if k in environ]
