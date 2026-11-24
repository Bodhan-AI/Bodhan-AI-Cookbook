"""synthesize_speech: sentence-chunked TTS, concatenated, optional MP3, labelled AI-generated."""
from __future__ import annotations

import asyncio
import io
import json
import re
import wave

from .. import core
from ..core import PRICES, ToolError
from ..languages import ALL_VOICES, SPEAK, norm, pick_voice, script_counts
from .audio import _run
from .translate import split_text

CHUNK_CHARS = 220  # ~25 s of speech with margin; tune per language after measuring
STYLES = {"AIR style news", "Customer Care", "TV style news", "advertisements", "anger", "children's stories",
          "disgust", "educational lecture", "fear", "happy", "news", "sad",
          "single person narration audiobook", "surprise"}


CAP_S = 29.5  # measured (scripts/probe_limits.py): longer input still returns 30.5 s, the tail is silently lost


def _valid_wav(b: bytes) -> bool:
    try:
        with wave.open(io.BytesIO(b)) as w:
            return w.getnframes() > 0
    except (wave.Error, EOFError):
        return False


def _seconds(b: bytes) -> float:
    with wave.open(io.BytesIO(b)) as w:
        return w.getnframes() / w.getframerate()


def _concat(wavs: list[bytes], gap_s: float = 0.15) -> tuple[bytes, list[float]]:
    """Join PCM16 mono WAVs with silence. Returns data and each chunk's start time."""
    params, frames, starts, t = None, [], [], 0.0
    for b in wavs:
        with wave.open(io.BytesIO(b)) as w:
            if params is None:
                params = w.getparams()
            elif (w.getnchannels(), w.getsampwidth(), w.getframerate()) != params[:3]:
                raise ToolError("UPSTREAM_ERROR", "speech chunks have inconsistent audio formats")
            fr = w.readframes(w.getnframes())
            starts.append(round(t, 3))
            t += w.getnframes() / w.getframerate() + gap_s
            frames.append(fr + b"\x00" * (int(gap_s * w.getframerate()) * w.getsampwidth() * w.getnchannels()))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as o:
        o.setparams(params)
        o.writeframes(b"".join(frames))
    return buf.getvalue(), starts


def _key(text: str, voice: str, lang: str, style: str | None) -> str:
    ins = {"lang": lang} | ({"style": style} if style else {})
    return core.key_of("speak", voice, json.dumps(ins, sort_keys=True), text)


async def _chunk(text: str, voice: str, lang: str, style: str | None, redo: bool, stats: dict) -> bytes:
    ins = {"lang": lang} | ({"style": style} if style else {})
    key = _key(text, voice, lang, style)
    if not redo:
        c = core.cache_get(key)
        if c is not None and _valid_wav(c):  # a damaged cache entry is ignored and synthesised again, never played
            stats["cached"] += 1
            return c
    cost = len(text) / 10000 * PRICES["speak_per_10k_chars"]
    core.reserve(cost)
    try:
        r = await core.post("speak", "/v1/audio/speech", json={"model": "indic-speak", "input": text, "voice": voice,
                                                             "instructions": json.dumps(ins)})
        core.record("speak", cost, f"{len(text)} chars")
    finally:
        core.release(cost)
    stats["cost"] += cost
    if not _valid_wav(r.content):
        raise ToolError("UPSTREAM_ERROR", "Indic-Speak returned audio that is not a valid WAV", "Retry; if it repeats, shorten the text.", retryable=True)
    core.cache_put(key, r.content)
    return r.content


def _check_script(text: str, lang: str) -> None:
    """Refuse to spend on text whose script cannot be `lang`: English with language=ta, or Tamil with language=en."""
    sc = script_counts(text)
    if not sc:
        return
    top = max(sc, key=sc.get)
    if lang != "en" and top == "Latin" and sc[top] > 0.8 * sum(sc.values()):
        raise ToolError("INVALID_ARGUMENT", f"the text is in Latin script but language='{lang}'",
                        "Call translate_text first and pass its translation_ref, or set language='en'.")
    if lang == "en" and top != "Latin":
        raise ToolError("INVALID_ARGUMENT", f"the text is in {top} script but language='en'", "Set the language the text is written in.")


async def synthesize_speech(text: str | None = None, text_ref: str | None = None, language: str = "hi",
                            voice: str | None = None, gender: str | None = None, style: str | None = None,
                            format: str = "mp3", out_name: str | None = None,
                            redo_chunks: list[int] | None = None) -> dict:
    if bool(text) == bool(text_ref):
        raise ToolError("INVALID_ARGUMENT", "provide exactly one of text or text_ref")
    lang = norm(language)
    if lang not in SPEAK:
        raise ToolError("UNSUPPORTED_LANGUAGE", f"'{language}' is not supported by indic-speak",
                        f"Supported: {', '.join(sorted(SPEAK))}")
    if format not in ("mp3", "wav"):
        raise ToolError("INVALID_ARGUMENT", "format must be mp3 or wav")
    if style and style not in STYLES:
        raise ToolError("INVALID_ARGUMENT", f"unknown style '{style}'", f"Valid: {sorted(STYLES)}")
    if voice:
        v = ALL_VOICES.get(voice.lower())
        if not v:
            raise ToolError("INVALID_ARGUMENT", f"unknown voice '{voice}'", "Call list_voices for the library.")
        voice = v
    else:
        voice = pick_voice(lang, gender)
    src = text if text else core.read_text(core.resolve_in(text_ref))
    src = src.strip()
    if not src:
        raise ToolError("EMPTY_INPUT", "nothing to speak")
    pieces = []
    for para in src.splitlines():
        pieces += split_text(para.strip(), CHUNK_CHARS) if para.strip() else []
    redo = set(redo_chunks or [])
    _check_script(src, lang)
    uniq = {p: i for i, p in reversed(list(enumerate(pieces)))}  # first index of each distinct chunk text
    est = sum(len(p) for p, i in uniq.items() if i in redo or core.cache_get(_key(p, voice, lang, style)) is None) \
        / 10000 * PRICES["speak_per_10k_chars"]  # cached chunks cost nothing
    core.check_budget(est)  # whole-job pre-flight: TTS is the expensive call
    stats = {"cost": 0.0, "cached": 0}
    first = {}  # identical chunk text is synthesised once per run
    for i, p in enumerate(pieces):
        first.setdefault(p, i)
    done = dict(zip(first, await asyncio.gather(*[_chunk(p, voice, lang, style, i in redo, stats) for p, i in first.items()])))
    wavs = [done[p] for p in pieces]
    data, starts = _concat(list(wavs))
    capped = [i for i, b in enumerate(wavs) if _seconds(b) >= CAP_S]  # the API cuts audio at ~30 s without an error
    base = out_name or f"speech_{core.key_of(src, voice, lang, style or '')[:10]}"
    base = base.rsplit(".", 1)[0]
    wav_p = core.out_path(base + ".wav")
    wav_p.write_bytes(data)
    final = wav_p
    if format == "mp3":
        final = core.out_path(base + ".mp3")
        r = await asyncio.to_thread(_run, ["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav_p), "-b:a", "192k",
                                           "-metadata", "comment=AI-generated speech (Bodhan Indic-Speak)", str(final)])
        if r.returncode != 0:
            raise ToolError("INTERNAL", f"mp3 encoding failed: {r.stderr[-200:]}")
        wav_p.unlink()
    with wave.open(io.BytesIO(data)) as w:
        dur = w.getnframes() / w.getframerate()
    cmap = core.write_text(base + ".chunks.json", json.dumps(
        [{"i": i, "start": s, "text": p} for i, (s, p) in enumerate(zip(starts, pieces))], ensure_ascii=False, indent=1))
    return {"audio_ref": str(final), "format": format, "duration_s": round(dur, 2), "voice": voice,
            "language": lang, "style": style, "chunks": len(pieces), "chars": len(src),
            "chunk_map_ref": str(cmap), "ai_generated": True, "cached_chunks": stats["cached"],
            "cost_inr": round(stats["cost"], 4),
            "warnings": ([f"chunk(s) {capped} reached the API's ~30 s audio cap and were probably cut off; split the text into shorter paragraphs"]
                         if capped else []) + (["input contains digits: Indic-Speak reads them in English; spell numbers out in the target language for natural speech"]
                         if re.search(r"\d", src) else [])}
