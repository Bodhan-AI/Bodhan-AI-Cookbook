"""Stages 6-8: speak each unit, check it by back-transcription, fit it into its time window."""

import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import audio
from .bodhan import Bodhan
from .llm import LLM, LLMError
from .text import chunks, compare, digits, native_digits, numbers
from .translate import translate_text

log = logging.getLogger(__name__)

WORKERS = 4  # concurrent units per language
MAX_SPEEDUP = 1.2  # never speed speech up more than this, if shortening can avoid it
MAX_BORROW = 2.0  # seconds of the following pause a unit may run into
EARLY = 0.2  # seconds a unit may start before its source, into a silent pause (sync target is +-0.3 s)
GUARD = 0.15  # seconds kept free between two units
SHORTEN_TARGET = 0.9  # ask for this share of the words that would exactly fit, to leave some slack
MIN_KEEP = 0.6  # a shortened sentence keeps at least this share of the original's words; past that, speed up
MAX_SHORTEN = 2  # rounds of "shorten the English, translate again"
SHORTEN_BATCH = 20  # sentences per shortening call at most; halved on any batch that fails
TTS_TRIES = 3  # attempts per chunk when the back-transcription looks wrong
MAX_MISSING = 0.25  # regenerate a chunk if more than this share of its text was not heard (omission)
MAX_EXTRA = 1.5  # ... or if it was heard this much longer than expected (repetition)
TTS_CHUNK_CHARS = 160  # Indic-Speak works best on a sentence or two

SHORTEN_PROMPT = """These English sentences will be dubbed, but their translations run too long for their time slots.
Rewrite each sentence in about its "words" number of words, and never fewer than its "min_words". If a
sentence cannot be that short and still be a clear, grammatical sentence with all of its meaning, make it
longer: a slightly long sentence is fine, a garbled one is not. Keep its meaning, every number (as digits), and
every technical term and letter label exactly. Do not add anything that is not in the sentence. Treat each
sentence on its own: never move content from one sentence to another.
The result is translated and read aloud, so write plain, grammatical spoken English: whole sentences, no
symbols such as < > = ° % / or arrows, and no abbreviations that are not in the original.
Reply with JSON only, one entry per sentence, with the same ids: {"sentences": [{"id": 0, "text": "..."}]}

Sentences:
ITEMS"""

SHORTEN_RETRY = """

Your earlier answers for these sentences changed a number or cut too much. Use exactly the numbers listed
with each one, as digits, and at least its "min_words" words."""


@dataclass
class _Unit:
    """One unit on its way through the speak / shorten rounds."""

    row: dict
    voice: str
    start: float  # earliest start of the dub
    budget: float  # seconds the dub may last
    source: str  # the English being spoken: the original, or the latest shortened version
    qa: list[str]
    rounds: list[dict] = field(default_factory=list)
    error: str = ""

    def too_long(self) -> bool:
        return not self.error and self.rounds[-1]["duration"] > self.budget * MAX_SPEEDUP


def speak(rows: list[dict], voices: list[str], lang: str, total: float, folder: Path,
          bodhan: Bodhan, llm: LLM, script: str, keep: list[str]) -> list[dict]:
    """Speak, check and fit every unit. A unit that fails is skipped with its reason; the rest go on.

    A unit's window is its own time, plus up to EARLY seconds of the pause before it and up to
    MAX_BORROW seconds of the pause after it (see window). Every unit is spoken first. Then all units
    whose speech is too long for their window even at MAX_SPEEDUP are shortened together, in one LLM
    call per batch, and translated and spoken again. That repeats up to MAX_SHORTEN rounds. Whatever
    is still over is sped up to fit, and reported.
    """
    clips = folder / ".clips"
    clips.mkdir(parents=True, exist_ok=True)
    units = []
    for i, row in enumerate(rows):
        start, end = window(rows, i, total)
        units.append(_Unit(row, voices[i], start, end - start, row["source"], list(row["translation_qa"])))

    def speak_round(pool: ThreadPoolExecutor, n: int, todo: list[_Unit]) -> None:
        def run(u: _Unit) -> None:
            try:
                text = translate_text(u.source, keep, lang, bodhan, script) if n else u.row["translation"]
                wav, checks = speak_checked(text, lang, u.voice, clips / f"unit_{u.row['id']:03d}_r{n}.wav", bodhan)
                u.rounds.append({"source": u.source, "translation": text, "wav": wav,
                                 "duration": audio.wav_duration(wav), "checks": checks})
                log.info("[%s] unit %d: spoken, %.2fs for a %.2fs window (round %d)", lang, u.row["id"],
                         u.rounds[-1]["duration"], u.budget, n, extra={"lang": lang, "unit": u.row["id"]})
            except Exception as e:  # one unit failing must not lose the whole language
                if n == 0:
                    u.error = f"{type(e).__name__}: {e}"
                    log.error("[%s] unit %d skipped: %s", lang, u.row["id"], e, extra={"lang": lang, "unit": u.row["id"]})
                else:  # keep what the earlier rounds made
                    u.qa.append(f"shortening round {n} failed: {type(e).__name__}: {e}")
                    log.warning("[%s] unit %d: round %d failed (%s); keeping round %d", lang, u.row["id"], n, e,
                                n - 1, extra={"lang": lang, "unit": u.row["id"]})
        list(pool.map(run, todo))

    with ThreadPoolExecutor(WORKERS) as pool:
        todo = units
        for n in range(MAX_SHORTEN + 1):
            speak_round(pool, n, todo)
            over = [u for u in todo if len(u.rounds) == n + 1 and u.too_long()]
            if not over or n == MAX_SHORTEN:
                break
            log.info("[%s] round %d: shortening %d unit(s) that run too long", lang, n + 1, len(over),
                     extra={"lang": lang})
            requests, asked = [], []
            for u in over:
                floor = min_words(u.row["source"])
                if len(u.source.split()) <= floor:  # cutting more would garble it: speed it up instead
                    u.qa.append(f"not shortened further: already down to {MIN_KEEP:.0%} of the original words")
                    continue
                words = max(floor, target_words(u.source, u.rounds[-1]["duration"], u.budget))
                requests.append((u.source, words, floor))
                asked.append(u)
            todo = []
            for u, shorter in zip(asked, shorten_all(requests, llm), strict=True):
                if shorter:
                    u.source = shorter
                    todo.append(u)
                    continue
                why = "LLM unavailable" if llm.down else "LLM failed, changed the numbers or cut too much"
                log.warning("[%s] unit %d: could not shorten (%s)", lang, u.row["id"], why,
                            extra={"lang": lang, "unit": u.row["id"]})
                u.qa.append(f"not shortened: {why}")
        return list(pool.map(lambda u: _finish(u, lang, clips), units))


def min_words(original: str) -> int:
    """The fewest words a shortened version of the original English sentence may have."""
    return max(3, math.ceil(len(original.split()) * MIN_KEEP))


def target_words(source: str, duration: float, budget: float) -> int:
    """How many words the shortened English should have, for speech of `duration` to fit `budget`."""
    return max(3, int(len(source.split()) * budget / duration * SHORTEN_TARGET))


def _finish(u: _Unit, lang: str, clips: Path) -> dict:
    """Pick the shortest round, speed it up if it still does not fit, and report the unit."""
    row = u.row
    if u.error:
        return {**row, "voice": u.voice, "skipped": u.error}
    best = min(u.rounds, key=lambda r: r["duration"])
    speed = max(1.0, best["duration"] / u.budget)
    final = clips / f"unit_{row['id']:03d}.wav"
    if speed > 1.0:
        audio.speed_up(best["wav"], final, speed)
    else:
        final.write_bytes(best["wav"].read_bytes())

    qa = list(u.qa)
    if speed > MAX_SPEEDUP + 0.005:
        qa.append(f"sped up {speed:.2f}x, above the {MAX_SPEEDUP}x target")
    cer = compare(best["translation"], " ".join(c["heard"] for c in best["checks"]), latin=lang == "en")["cer"]
    bad = [c for c in best["checks"] if not c["ok"]]
    if bad:
        qa.append(f"{len(bad)} chunk(s) still look repeated or truncated after {TTS_TRIES} tries")

    result = {**row, "source_spoken": best["source"], "translation": best["translation"], "voice": u.voice,
              "budget": round(u.budget, 3), "speech_duration": round(best["duration"], 3),
              "speed": round(speed, 3), "dub_start": round(u.start, 3),
              "dub_end": round(u.start + best["duration"] / speed, 3),
              "shorten_rounds": len(u.rounds) - 1, "tts_attempts": sum(c["attempts"] for c in best["checks"]),
              "cer": round(cer, 3), "chunks": best["checks"], "qa": qa, "clip": str(final)}
    trace = {k: result[k] for k in ("voice", "budget", "speech_duration", "speed", "dub_start", "dub_end",
                                    "shorten_rounds", "tts_attempts", "cer", "qa")}
    log.info("[%s] unit %d: %.2fs speech, budget %.2fs, speed %.2fx, CER %.2f, %d round(s)",
             lang, row["id"], best["duration"], u.budget, speed, cer, len(u.rounds),
             extra={"lang": lang, "unit": row["id"], "data": trace})
    return result


def window(rows: list[dict], i: int, total: float) -> tuple[float, float]:
    """Earliest start and latest end for unit i's dub.

    The pause between two units is shared: the later unit may start up to EARLY seconds into it,
    the earlier one may run up to MAX_BORROW seconds into it, and GUARD seconds stay silent between.
    """
    def early(gap: float) -> float:
        return min(EARLY, max(0.0, (gap - GUARD) / 2))

    row = rows[i]
    prev_end = rows[i - 1]["end"] if i else 0.0
    next_start = rows[i + 1]["start"] if i + 1 < len(rows) else total
    next_early = early(next_start - row["end"]) if i + 1 < len(rows) else 0.0
    start = row["start"] - early(row["start"] - prev_end)
    end = min(next_start - next_early - GUARD, row["end"] + MAX_BORROW, total)
    return start, max(end, row["end"])


def shorten_all(items: list[tuple[str, int, int]], llm: LLM) -> list[str | None]:
    """Shorter English for many sentences at once: (sentence, target words, fewest words) in, one answer
    or None out.

    One LLM call per SHORTEN_BATCH sentences. A batch whose call fails (its reply cut off at the
    model's output limit, say, as reasoning models do on long batches) is split in half and each half
    asked again, so any model gets the largest batches it can answer. Each answer is checked on its
    own: every number must survive, and it may not cut below the fewest words (one word of slack).
    Sentences whose answer failed the check get one more batched try, with their numbers spelled out;
    whatever still fails is None, and the caller speeds that unit up.
    """
    result: list[str | None] = [None] * len(items)

    def ask(batch: list[int], retry: bool) -> None:
        lines = []
        for i in batch:
            text, words, fewest = items[i]
            item = {"id": i, "words": words, "min_words": fewest, "text": text}
            if retry:
                item["numbers"] = numbers(digits(items[i][0]))
            lines.append(json.dumps(item, ensure_ascii=False))
        prompt = SHORTEN_PROMPT.replace("ITEMS", "\n".join(lines)) + (SHORTEN_RETRY if retry else "")
        try:
            answers = _answers(llm.ask_json(prompt))
        except LLMError as e:
            if len(batch) > 1 and not llm.down:
                log.info("shortening %d sentence(s) failed (%s); asking again in two halves", len(batch), e)
                ask(batch[:len(batch) // 2], retry)
                ask(batch[len(batch) // 2:], retry)
            else:
                log.warning("shortening %d sentence(s) failed: %s", len(batch), e)
            return
        for i in batch:
            shorter = answers.get(i, "")
            if shorter and numbers_kept(shorter, items[i][0]) and len(shorter.split()) >= items[i][2] - 1:
                result[i] = shorter

    pending = list(range(len(items)))
    for retry in (False, True):
        for b in range(0, len(pending), SHORTEN_BATCH):
            ask(pending[b:b + SHORTEN_BATCH], retry)
        pending = [i for i in pending if result[i] is None]
        if not pending or llm.down:
            break
    return result


def numbers_kept(shorter: str, source: str) -> bool:
    """The shortened sentence has the same numbers as the source. A 1 may go: "name one pair" is fairly
    shortened to "name a pair", and a lost "1" changes the meaning far less often than other numbers."""
    def kept(text: str) -> list[int]:
        return [n for n in numbers(digits(text)) if n != 1]
    return kept(shorter) == kept(source)


def _answers(reply: dict) -> dict[int, str]:
    """id -> text from a shortening reply, ignoring entries that are malformed."""
    answers = {}
    for entry in reply.get("sentences") or []:
        try:
            answers[int(entry["id"])] = str(entry["text"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
    return answers


def speak_checked(text: str, lang: str, voice: str, out: Path, bodhan: Bodhan) -> tuple[Path, list[dict]]:
    """Speak text chunk by chunk. Back-transcribe each chunk and regenerate it if it does not match
    (Indic-Speak sometimes repeats or drops words). Keeps the best of TTS_TRIES attempts."""
    parts, checks = [], []
    for k, chunk in enumerate(chunks(text, TTS_CHUNK_CHARS)):
        chunk = native_digits(chunk, lang)
        best = None
        for attempt in range(TTS_TRIES):
            wav = bodhan.speak(chunk, lang, voice, out.with_name(f"{out.stem}_c{k}_a{attempt}.wav"), attempt)
            seconds = audio.wav_duration(wav)
            heard = bodhan.transcribe(wav, lang, seconds) if seconds < 30 else ""
            check = {"text": chunk, "heard": heard, **compare(chunk, heard, latin=lang == "en"), "attempts": attempt + 1}
            check["ok"] = check["missing"] <= MAX_MISSING and check["extra"] <= MAX_EXTRA
            score = check["missing"] + max(0.0, check["extra"] - 1)
            if best is None or score < best[2]:
                best = (wav, check, score)
            if check["ok"]:
                break
        parts.append(best[0])
        checks.append(best[1])
    audio.join(parts, out)
    return out, checks
