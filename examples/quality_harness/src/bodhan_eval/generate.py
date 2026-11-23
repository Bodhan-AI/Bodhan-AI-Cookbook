"""--generate: fill in missing translations and audio with Bodhan models. Supplied outputs are never replaced.

Translations are cheap and generated for every item that lacks one. Audio is the expensive step
(and Indic-Speak allows ~7 requests/min), so at most `max_items` items are voiced, until the budget
runs out; the rest are marked `not_sampled`. Per language the order alternates the riskiest item
left (numbers, Latin-script names, several chunks, repeated words, symbols: what TTS gets wrong)
with a seeded random one, so half the sample is unbiased; languages are interleaved. The order and
the cut-off depend only on the dataset, the budget and max_items, never on what is cached, so a
rerun selects exactly the same items. The CLI runs generate_audio alongside the text checks.
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import wave
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from itertools import chain, zip_longest
from pathlib import Path

from .clients import (
    DEFAULT_VOICE, INR_PER_AUDIO_MINUTE, INR_PER_SPEAK_CHAR, BudgetExceeded, Clients, ModelError,
)
from .dataset import Item
from .segment import chunks, speakable

log = logging.getLogger("bodhan_eval.generate")

VOICES = {"hi": "Kavya", "ta": "Anitha", "bn": "Ishita"}  # others fall back to DEFAULT_VOICE
MAX_CHUNK_CHARS = 250  # about 25 s of speech; Indic-Speak takes about 30 s per request
CHARS_PER_SECOND = 10  # Indic-Speak speaking rate, measured: 95 characters -> 9.2 s
TEXT_RESERVE_INR = 0.002  # per item, kept back from the audio budget for translation and back-translation
WORKERS = 8
SEED = 0


def mark_supplied(items: list[Item]) -> None:
    for item in items:
        item.status.setdefault("translation", "supplied" if item.translation is not None else "missing")
        item.status.setdefault("audio", "supplied" if item.audio is not None else "missing")


MAX_SPEECH_ITEMS = 30


def generate(items: list[Item], clients: Clients, audio_dir: Path, budget_inr: float | None, speech: bool = True,
             max_items: int | None = MAX_SPEECH_ITEMS) -> dict:
    mark_supplied(items)
    if clients.available("translate"):
        todo = [i for i in items if i.translation is None]
        with ThreadPoolExecutor(WORKERS) as pool:
            list(pool.map(lambda i: _translate(i, clients), todo))
    if speech:
        generate_audio(items, clients, audio_dir, budget_inr, max_items)
    return status_counts(items)


def status_counts(items: list[Item]) -> dict:
    return {field: dict(sorted(Counter(i.status[field] for i in items).items())) for field in ("translation", "audio")}


def _translate(item: Item, clients: Clients) -> None:
    try:
        out = clients.translate(item.source, item.lang)
    except BudgetExceeded:
        item.status["translation"] = "skipped_budget"
        return
    except ModelError as e:
        item.status["translation"] = "error"
        item.warnings.append(f"translation failed: {e}")
        return
    item.translation, item.status["translation"] = out.text, "generated"
    if out.finish_reason != "stop":
        item.warnings.append(f"generated translation was cut off (finish_reason={out.finish_reason})")


def generate_audio(items: list[Item], clients: Clients, audio_dir: Path, budget_inr: float | None,
                   max_items: int | None = MAX_SPEECH_ITEMS) -> None:
    if not clients.available("speak"):
        return
    todo = []
    for item in items:
        if item.audio is not None or item.translation is None:
            continue
        if not speakable(item.translation).strip():
            item.status["audio"] = "empty"
        else:
            todo.append(item)

    selected, estimate = select_for_audio(todo, None if budget_inr is None else budget_inr - TEXT_RESERVE_INR * len(items),
                                          max_items)
    chosen = {i.key for i in selected}
    for item in todo:
        if item.key not in chosen:
            item.status["audio"] = "not_sampled"
    log.info("speech: voicing %d of %d items, estimated ₹%.2f before cache hits", len(selected), len(todo), estimate)

    audio_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(lambda i: _speak(i, clients, audio_dir), selected))


def audio_cost(text: str) -> float:
    """Estimated cost of voicing a text and transcribing it back."""
    n = len(speakable(text))
    return n * INR_PER_SPEAK_CHAR + n / CHARS_PER_SECOND / 60 * INR_PER_AUDIO_MINUTE


def risk(text: str) -> int:
    """How likely speech synthesis is to go wrong on this text: one point per known trigger."""
    t = speakable(text)
    words = [w for w in t.split() if len(w) >= 3]
    return (any(c.isdigit() for c in t) + bool(re.search(r"[A-Za-z]{2,}", t)) + (len(chunks(t, MAX_CHUNK_CHARS)) > 1)
            + (len(words) != len(set(words))) + bool(re.search(r"[()\[\]/%°+=]", t)))


def select_for_audio(items: list[Item], budget_inr: float | None, max_items: int | None = None) -> tuple[list[Item], float]:
    """Items to voice, in order: per language riskiest and random alternately, languages interleaved,
    cut at the budget or max_items."""
    by_lang: dict[str, list[Item]] = {}
    for item in sorted(items, key=lambda i: i.key):  # dataset line order must not matter
        by_lang.setdefault(item.lang, []).append(item)
    rng = random.Random(SEED)
    for lang in sorted(by_lang):
        group = by_lang[lang]
        shuffled = rng.sample(group, len(group))
        score = {i.key: risk(i.translation or "") for i in group}
        risky = sorted(group, key=lambda i: (-score[i.key], i.key))  # ties by id, not the random order
        merged, seen = [], set()
        for pair in zip(risky, shuffled):
            for item, queue in zip(pair, ("risk", "random")):
                if item.key not in seen:
                    seen.add(item.key)
                    item.status["audio_sample"] = "risk" if queue == "risk" and score[item.key] else "random"
                    merged.append(item)
        by_lang[lang] = merged
    order = [i for i in chain.from_iterable(zip_longest(*(by_lang[k] for k in sorted(by_lang)))) if i is not None]

    selected, total = [], 0.0
    for item in order:
        cost = audio_cost(item.translation or "")
        if (budget_inr is not None and total + cost > budget_inr) or (max_items is not None and len(selected) >= max_items):
            break  # stop at the first item that doesn't fit, so the sample stays interleaved
        selected.append(item)
        total += cost
    for item in items:
        if item not in selected:
            item.status.pop("audio_sample", None)
    return selected, total


def _speak(item: Item, clients: Clients, audio_dir: Path) -> None:
    lang = item.lang.split("-")[0]  # Indic-Speak takes base codes only (mni, not mni-Beng)
    voice = VOICES.get(lang, DEFAULT_VOICE)
    parts = chunks(speakable(item.translation or ""), MAX_CHUNK_CHARS)
    try:
        wavs = [clients.speak(p, lang, voice) for p in parts]
        path = audio_dir / audio_name(item)
        times = join_wavs(wavs, path)
    except BudgetExceeded:
        item.status["audio"] = "skipped_budget"
        return
    except (ModelError, wave.Error, ValueError) as e:
        item.status["audio"] = "error"
        item.warnings.append(f"speech generation failed: {e}")
        return
    item.audio, item.status["audio"] = path, "generated"
    item.audio_segments = [{"text": p, "start_s": round(s, 3), "end_s": round(e, 3)} for p, (s, e) in zip(parts, times)]


def audio_name(item: Item) -> str:
    safe = re.sub(r"[^\w.-]", "_", item.id)
    if safe != item.id:  # keep "a/b" and "a_b" from sharing a file
        safe += "-" + hashlib.sha256(item.id.encode()).hexdigest()[:8]
    return f"{safe}.{item.lang}.wav"


def join_wavs(paths: list[Path], out: Path) -> list[tuple[float, float]]:
    """Concatenate WAVs with the same format into `out`; returns each part's (start, end) in seconds."""
    frames, times, fmt, t = [], [], None, 0.0
    for p in paths:
        with wave.open(str(p), "rb") as w:
            this = (w.getnchannels(), w.getsampwidth(), w.getframerate())
            if fmt is None:
                fmt = this
            elif this != fmt:
                raise ValueError(f"{p.name}: format {this} differs from {fmt}")
            n = w.getnframes()
            frames.append(w.readframes(n))
            times.append((t, t + n / this[2]))
            t += n / this[2]
    if fmt is None:
        raise ValueError("no audio to join")
    with wave.open(str(out), "wb") as w:
        w.setnchannels(fmt[0])
        w.setsampwidth(fmt[1])
        w.setframerate(fmt[2])
        w.writeframes(b"".join(frames))
    return times
