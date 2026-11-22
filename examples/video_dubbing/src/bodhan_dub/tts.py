"""Synthesize speech with Indic-Speak (hosted API). Results are cached on disk by (text, voice, lang, style)."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

import soundfile as sf

from .api import post_with_retry
from .config import BASE_URL, get_key

log = logging.getLogger(__name__)

MODEL = "indic-speak"
URL = f"{BASE_URL}/v1/audio/speech"


def cache_key(text: str, voice: str, lang: str, style: str | None, variant: int = 0) -> str:
    """`variant` lets a retry bypass the cache and get a fresh sample of the same text."""
    raw = json.dumps([MODEL, text, voice, lang, style, variant], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def synthesize(
    text: str,
    voice: str,
    lang: str,
    out_dir: str | Path,
    style: str | None = None,
    variant: int = 0,
    key: str | None = None,
) -> dict:
    """One TTS call (1-2 sentences). Returns {ok, path, dur, chars, cached, sec, error?}.

    Output is the API's WAV (24 kHz mono PCM16), saved as <out_dir>/tts_<hash>.wav.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"tts_{cache_key(text, voice, lang, style, variant)}.wav"
    chars = len(text)

    if path.is_file():
        info = sf.info(str(path))
        return {"ok": True, "path": str(path), "dur": round(info.duration, 3), "chars": chars, "cached": True, "sec": 0.0}
    if not text.strip():
        return {"ok": False, "path": None, "dur": 0.0, "chars": 0, "cached": False, "sec": 0.0, "error": "empty text"}

    instructions = {"lang": lang}
    if style:
        instructions["style"] = style
    t0 = time.time()
    try:
        r = post_with_retry(URL, key or get_key("speak"), json={
            "model": MODEL, "input": text, "voice": voice,
            "instructions": json.dumps(instructions, ensure_ascii=False)})
    except Exception as e:
        return {"ok": False, "path": None, "dur": 0.0, "chars": chars, "cached": False,
                "sec": round(time.time() - t0, 2), "error": f"{type(e).__name__}: {e}"}
    sec = round(time.time() - t0, 2)
    if not r.ok:
        return {"ok": False, "path": None, "dur": 0.0, "chars": chars, "cached": False, "sec": sec,
                "error": f"HTTP {r.status_code}: {r.text[:200]}"}

    path.write_bytes(r.content)
    try:
        dur = sf.info(str(path)).duration
    except Exception as e:  # not a readable WAV
        path.unlink(missing_ok=True)
        return {"ok": False, "path": None, "dur": 0.0, "chars": chars, "cached": False, "sec": sec,
                "error": f"unreadable audio: {e}"}
    log.info("tts voice=%s lang=%s chars=%d dur=%.2fs api_sec=%.1f", voice, lang, chars, dur, sec)
    return {"ok": True, "path": str(path), "dur": round(dur, 3), "chars": chars, "cached": False, "sec": sec}
