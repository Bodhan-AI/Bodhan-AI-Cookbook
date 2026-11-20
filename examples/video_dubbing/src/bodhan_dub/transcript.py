"""Stages 1-4: audio, optional voice/music separation, speech regions, transcription, and the LLM plan."""

import json
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import audio
from .bodhan import Bodhan
from .llm import LLM, LLMError
from .text import digits, numbers

log = logging.getLogger(__name__)

PAD = 0.15  # seconds of context around each region sent to transcription
WORKERS = 4  # concurrent API calls
MAX_GAP = 1.5  # seconds; a longer pause always ends a unit, so silences stay silent
PLAN_CHARS = 4000  # transcript characters per plan call: 4-8 minutes of speech, a reply of about 2k tokens


@dataclass
class Speech:
    segments: list[dict]  # transcribed speech regions: id, start, end, text
    wav: Path  # 16 kHz speech audio; the vocals stem when the soundtrack was separated
    total: float  # seconds: the video's duration, which the dubbed track must match
    background: Path | None = None  # music and effects stem, if separated
    skipped: list[dict] = field(default_factory=list)  # regions with no transcribed speech


def transcribe(video: Path, out: Path, bodhan: Bodhan, separate: str = "auto") -> Speech:
    """Extract audio, separate voice from music if needed, find speech regions, transcribe each.

    Indic-Transcribe returns text only, so a segment's timestamps are the boundaries of the
    speech region it came from. With separation, VAD and transcription run on the clean vocals,
    and the music stem is kept for the final mix.
    """
    work = out / ".work"
    work.mkdir(parents=True, exist_ok=True)

    wav = work / "audio.wav"
    audio.extract_audio(video, wav)
    total = audio.duration(video)  # the container, not the audio stream, which can be shorter
    regions = audio.speech_regions(wav)

    background = None
    if separate == "on" or (separate == "auto" and audio.has_background(wav, regions)):
        log.info("separating voice from background audio with Demucs (about real time on CPU)")
        vocals, background = audio.separate(video, work)
        audio.extract_audio(vocals, wav)
        regions = audio.speech_regions(wav)
    else:
        log.info("no background audio to keep (separate=%s); skipping separation", separate)
    log.info("found %d speech regions in %.1fs of audio", len(regions), total)

    def run(item):
        i, (start, end) = item
        clip = work / f"region_{i:03d}.wav"
        a, b = max(0.0, start - PAD), min(total, end + PAD)
        audio.cut(wav, a, b, clip)
        return bodhan.transcribe(clip, "en", seconds=b - a)

    with ThreadPoolExecutor(WORKERS) as pool:
        texts = list(pool.map(run, enumerate(regions)))

    speech = Speech(segments=[], wav=wav, total=total, background=background)
    for (start, end), text in zip(regions, texts, strict=True):
        text = text.strip()
        if not text:
            speech.skipped.append({"start": round(start, 3), "end": round(end, 3), "reason": "no speech transcribed"})
            log.info("skip %.2f-%.2f: no speech transcribed", start, end, extra={"data": speech.skipped[-1]})
            continue
        speech.segments.append({"id": len(speech.segments), "start": round(start, 3), "end": round(end, 3), "text": text})

    log.info("transcribed %d segments", len(speech.segments))
    return speech


PLAN_PROMPT = """You are preparing an English video transcript for dubbing into other languages.

Below are speech segments from an automatic speech recogniser, in order: id, start and end time in seconds, text.
The recogniser adds no punctuation, sometimes writes in ALL CAPS, and sometimes mishears words.
KNOWN_TERMS
The recogniser cuts segments at pauses, and a pause often falls in the middle of a sentence:
"HERE THE POINTS A" then "O and B are collinear" is one sentence, "Here the points A, O and B are collinear."

1. Clean each segment's text in place.
   - Add punctuation and normal sentence casing. End a segment with a full stop, question mark or
     exclamation mark only if its sentence really ends there. A segment that continues a sentence starts
     with a lowercase word, unless that word is a name or a letter label.
   - Fix words the recogniser clearly misheard, using the context.
   - Write numbers as digits, e.g. "two hundred fifty grams" becomes "250 grams".
   - Where a word would be ambiguous to a translator, use the clearer word the context means,
     e.g. "bank" meaning the side of a river becomes "riverbank".
   - Keep every word in its own segment. Never move words to another segment, even to complete a sentence.
   - Do not add, drop, summarise or explain anything.

2. For each segment, say whether its text ends at the end of a sentence ("ends": true) or the sentence
   continues into the next segment ("ends": false), as when the next segment completes its grammar or a list.

3. List glossary terms: the subject's technical terms and key concepts, plus names, labels and acronyms,
   that must be translated the same way every time. Set "keep" to true for terms that must stay exactly
   as written in English, such as code, product names, unit symbols and letter labels; false otherwise.

Reply with JSON only, one entry per segment, in this shape:
{"segments": [{"id": 0, "text": "...", "ends": true}], "glossary": [{"term": "...", "keep": false}]}

Segments:
SEGMENTS"""


def plan(segments: list[dict], out: Path, llm: LLM, user_terms: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """Stage 4: the LLM cleans each segment's English in place and marks sentence ends; code builds units.

    A unit is a run of segments that ends where a sentence ends, never spanning a long pause.
    Words never move between segments, so each unit keeps the exact timing of its speech.
    The transcript goes to the LLM in windows of about PLAN_CHARS characters, split at the longest
    pauses, so each reply stays small whatever the video length or the LLM's output limit. Windows
    run in order, and each one is shown the terms found so far, so a term is spelled the same way
    throughout. A window whose call fails (its reply cut off at the model's output limit, say) is
    split in two at its longest pause and asked again. A single segment that still fails keeps its
    raw text, as its own unit: worse English, same timing. Terms from --glossary are always shown and always win over the LLM's own.
    """
    user_terms = user_terms or []
    found: dict[str, dict] = {}  # glossary terms from the LLM, by lowercase term
    cleaned: list[dict] = []
    queue, calls = plan_windows(segments), 0
    while queue:
        window = queue.pop(0)
        known = [t["term"] for t in user_terms] + [t["term"] for t in found.values()]
        calls += 1
        reply = _plan_window(window, known, llm)
        if reply is None and len(window) > 1 and not llm.down:
            cut = _longest_pause(window, len(window) // 4, max(1, len(window) - 1 - len(window) // 4))
            queue[:0] = [window[:cut], window[cut:]]
            log.info("plan: asking again for segments %d-%d in two parts", window[0]["id"], window[-1]["id"])
            continue
        reply = reply or {}
        ids = {s["id"] for s in window}
        cleaned += [c for c in reply.get("segments") or [] if isinstance(c, dict) and c.get("id") in ids]
        for t in reply.get("glossary") or []:
            if isinstance(t, dict) and isinstance(t.get("term"), str) and t["term"].strip():
                found.setdefault(t["term"].strip().lower(), {**t, "term": t["term"].strip()})
    units = build_units(segments, {"segments": cleaned})

    glossary = found | {t["term"].lower(): t for t in user_terms}
    glossary = list(glossary.values())

    (out / "transcript.en.json").write_text(json.dumps(units, indent=2, ensure_ascii=False))
    (out / "glossary.json").write_text(json.dumps(glossary, indent=2, ensure_ascii=False))
    log.info("planned %d units from %d segments in %d LLM call(s), %d glossary terms",
             len(units), len(segments), calls, len(glossary))
    return units, glossary


def _plan_window(window: list[dict], known: list[str], llm: LLM) -> dict | None:
    """The LLM's reply for one window, or None if the call failed."""
    terms = ""
    if known:
        terms = "These terms may appear; spell them exactly like this: " + ", ".join(known) + "\n"
    lines = "\n".join(f'{s["id"]} [{s["start"]:.1f}-{s["end"]:.1f}] {s["text"]}' for s in window)
    try:
        reply = llm.ask_json(PLAN_PROMPT.replace("KNOWN_TERMS", terms).replace("SEGMENTS", lines))
    except LLMError as e:
        log.warning("plan for segments %d-%d failed (%s)", window[0]["id"], window[-1]["id"], e)
        return None
    return reply if isinstance(reply, dict) else {}


def plan_windows(segments: list[dict], max_chars: int = PLAN_CHARS) -> list[list[dict]]:
    """Split segments into windows of at most max_chars of text, each cut at the longest pause in the
    second half of the window, so a cut rarely falls inside a sentence (a pause over MAX_GAP ends a
    unit anyway). A sentence that does cross a cut is still joined: units are built over all windows."""
    windows, i = [], 0
    while i < len(segments):
        end, size = i, 0
        while end < len(segments) and (end == i or size + len(segments[end]["text"]) <= max_chars):
            size += len(segments[end]["text"]) + 1
            end += 1
        if end < len(segments):  # not the last window: cut after the longest pause in its second half
            end = i + _longest_pause(segments[i:end + 1], (end - i) // 2, end - i)
        windows.append(segments[i:end])
        i = end
    return windows


def _longest_pause(segments: list[dict], lo: int, hi: int) -> int:
    """Where to cut segments in two (segments[:k], segments[k:]): after the segment j in [lo, hi) that
    has the longest pause after it, so k = j + 1. Needs hi < len(segments)."""
    lo = min(lo, hi - 1)
    return 1 + max(range(lo, hi), key=lambda j: segments[j + 1]["start"] - segments[j]["end"])


def build_units(segments: list[dict], reply: dict) -> list[dict]:
    """Join segments into units using the LLM's cleaned text and sentence ends, checked in code."""
    cleaned = {c.get("id"): c for c in reply.get("segments", []) if isinstance(c, dict)}
    units, current = [], []
    for seg in segments:
        c = cleaned.get(seg["id"], {})
        text = str(c.get("text", "")).strip()
        if not same_length(text, seg["text"]) or not small_numbers_kept(text, seg["text"]):
            if c:
                log.warning("segment %d: cleaned text rejected, using raw: %r", seg["id"], text[:60])
            text = seg["text"]
        current.append({**seg, "clean": text})

        nxt = segments[seg["id"] + 1] if seg["id"] + 1 < len(segments) else None
        long_pause = nxt is None or nxt["start"] - seg["end"] > MAX_GAP
        # The LLM may mark every segment as a sentence end yet start the next one in lowercase
        # ("and lie on opposite sides..."): that is a sentence going on, so join and drop the full stop.
        continues = not long_pause and str(cleaned.get(nxt["id"], {}).get("text", "")).strip()[:1].islower()
        if continues and current[-1]["clean"].endswith("."):
            current[-1]["clean"] = current[-1]["clean"][:-1]
        if (c.get("ends", True) and not continues) or long_pause:
            units.append({
                "id": len(units),
                "start": current[0]["start"],
                "end": current[-1]["end"],
                "text": " ".join(s["clean"] for s in current),
                "asr_text": " ".join(s["text"] for s in current),
                "segments": [s["id"] for s in current],
            })
            current = []
    return units


def small_numbers_kept(clean: str, raw: str) -> bool:
    """The cleaned text may not contain a small number (0-12) more often than the raw transcript.
    Catches the LLM inventing numbers. Larger numbers ("one eighty" -> 180) can't be checked this
    simply and are left alone."""
    have = Counter(n for n in numbers(digits(raw)) if n <= 12)
    want = Counter(n for n in numbers(clean) if n <= 12)
    return all(want[n] <= have[n] for n in want)


def same_length(clean: str, raw: str) -> bool:
    """Catch text that was dropped or moved between segments: word counts must roughly match.
    Numbers shrink when written as digits ("one hundred and ten" -> "110"), hence the loose bounds."""
    a, b = len(clean.split()), len(raw.split())
    return a > 0 and 0.6 * b - 1 <= a <= 1.3 * b + 1
