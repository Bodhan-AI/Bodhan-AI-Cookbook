"""``--generate``: fill in missing translations (Indic-Translate) and audio (Indic-Speak).

Speech synthesis is the expensive step (₹6 per 10,000 characters), so a planner
decides which items get audio:

* only items that have a translation and whose language has a voice;
* ``--speech-sample F`` keeps a fraction F per language (stratified);
* items are ranked by a deterministic risk score (long text, digits, repeated
  phrases, Markdown), ties broken by a hash of the id, so reruns pick the same set;
* the planner stops before the estimated synthesis + transcription cost would use
  more than 80 % of the remaining ``--max-cost-inr`` budget (the rest is left for
  back-translation and transcription checks).

Every decision is recorded and appears in the report.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from pathlib import Path

import numpy as np

from . import audio as A
from .checks.speech import speakable_for_tts
from .config import defaults, lang_info
from .models import Item

MAX_TTS_CHARS = 220


def tts_chunks(text: str, limit: int = MAX_TTS_CHARS) -> list[str]:
    """Split text into <= limit-character pieces at sentence, then clause, then word boundaries."""
    sentences = re.split(r"(?<=[.!?।॥])\s+", text)
    out: list[str] = []
    for s in sentences:
        while len(s) > limit:
            cut = max(s.rfind(", ", 0, limit), s.rfind("; ", 0, limit))
            if cut <= 0:
                cut = s.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit
            out.append(s[: cut + 1].strip())
            s = s[cut + 1 :].strip()
        if s:
            if out and len(out[-1]) + 1 + len(s) <= limit:
                out[-1] = f"{out[-1]} {s}"
            else:
                out.append(s)
    return [c for c in out if c]


def risk(item: Item) -> int:
    t = item.translation or ""
    words = t.split()
    rep = sum(c - 1 for c in Counter(zip(words, words[1:])).values() if c > 1)
    return int(len(t) > 300) + int(bool(re.search(r"\d", t))) + int(rep > 0) + int(bool(re.search(r"[|#$*]", t)))


def _h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:12], 16)


def speech_cost(item: Item) -> float:
    c = defaults()["costs_inr"]
    chars = len(speakable_for_tts(item.translation or ""))
    est_seconds = chars / 13.0 * 2  # first transcription + possible second opinion
    return chars * c["speak_per_char"] + est_seconds * c["transcribe_per_second"]


def plan_speech(
    items: list[Item], sample: float | None, remaining_budget: float | None
) -> tuple[list[Item], dict[str, str]]:
    decisions: dict[str, str] = {}
    by_lang: dict[str, list[Item]] = {}
    for it in items:
        if it.audio:
            continue
        if not it.translation:
            decisions[it.key] = "no audio: no translation to speak"
            continue
        if not lang_info(it.lang).voices:
            decisions[it.key] = f"no audio: no Indic-Speak voice for '{it.lang}'"
            continue
        by_lang.setdefault(it.lang, []).append(it)
    queues = []
    for _lang, its in sorted(by_lang.items()):
        its.sort(key=lambda x: (-risk(x), _h(x.key)))
        keep = len(its) if sample is None else max(1, math.ceil(sample * len(its)))
        for it in its[keep:]:
            decisions[it.key] = f"no audio: outside --speech-sample {sample}"
        queues.append(its[:keep])
    # round-robin across languages so a tight budget still covers every language
    order = []
    while any(queues):
        for q in queues:
            if q:
                order.append(q.pop(0))
    chosen, spend = [], 0.0
    cap = None if remaining_budget is None else 0.8 * remaining_budget
    for it in order:
        c = speech_cost(it)
        if cap is not None and spend + c > cap:
            decisions[it.key] = f"no audio: budget (would need ₹{c:.3f}, ₹{cap - spend:.3f} left for speech)"
            continue
        spend += c
        chosen.append(it)
        decisions[it.key] = f"audio generated (risk score {risk(it)})"
    return chosen, decisions


def synthesize(item: Item, ctx, out_dir: Path) -> str:
    info = lang_info(item.lang)
    voice = ctx.options.get("voices", {}).get(item.lang) or info.voices[0]
    text = speakable_for_tts(item.translation or "")
    pieces = []
    sr = 24000
    for chunk in tts_chunks(text):
        wav = ctx.bodhan.speak(chunk, info.tts_lang, voice)
        x, sr = A.load_audio_bytes(wav)
        pieces.append(x)
        pieces.append(A.silence(0.2, sr))
    if not pieces:
        raise ValueError("nothing speakable")
    y = np.concatenate(pieces[:-1])
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", item.id)
    path = out_dir / f"{safe}.{item.lang}.wav"
    A.write_wav(path, y, sr)
    return str(path.resolve())
