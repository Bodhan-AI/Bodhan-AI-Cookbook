"""Turn each inbox item into one analysed record: text, language, English, sentiment, themes, reply."""

from __future__ import annotations

import re
import sys
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .analyse import needs_reply, worst_theme
from .bodhan import BodhanError
from .ingest import Item, mask_pii
from .languages import dominant_script, language_for_script
from .respond import draft_reply, voice_reply

ENGLISH_SIMILARITY = 0.7
LOW_OCR_CONFIDENCE = 0.6
WORD = re.compile(r"[a-z0-9']+")
UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass(frozen=True)
class Options:
    out_dir: Path
    roman_default: str = "hi"
    speak: str = "voice"
    keep_pii: bool = False
    workers: int = 4


def word_overlap(first: str, second: str) -> float:
    a = set(WORD.findall(first.lower()))
    b = set(WORD.findall(second.lower()))
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def extract_text(item: Item, client, flags: list[str]) -> str:
    if item.channel == "text":
        return item.text
    if item.channel == "voice":
        return client.transcribe(item.file, item.language)
    page = client.ocr(item.file)
    confidence = page.get("confidence")
    if confidence is not None and confidence < LOW_OCR_CONFIDENCE:
        flags.append(f"low OCR confidence ({confidence:.2f}); check the card by eye")
    return page["text"]


def resolve_language(item: Item, text: str, client, options: Options, flags: list[str]) -> tuple[str, str | None, str, str]:
    script = dominant_script(text)
    if script is None:
        raise ValueError("no readable text was recovered")
    if script != "Latin":
        language = item.language or language_for_script(script, text)
        if language is None:
            raise ValueError(f"cannot map {script} script to a language; add a 'language' value for this row")
        return language, script, client.translate(text, "en"), "hint" if item.language else "script"
    if item.language == "en":
        return "en", script, text, "hint"
    english = client.translate(text, "en")
    if item.language:
        return item.language, script, english, "hint"
    if word_overlap(text, english) >= ENGLISH_SIMILARITY:
        return "en", script, text, "translation check"
    flags.append(f"roman-script text; language assumed to be '{options.roman_default}' (add a 'language' value to be sure)")
    return options.roman_default, script, english, "default"


def process_item(item: Item, client, analyser, taxonomy: dict, options: Options) -> dict:
    flags: list[str] = []
    record = {
        "id": item.id, "channel": item.channel, "file": item.file.name if item.file else None, "original": None,
        "language": item.language, "language_source": None, "script": None, "english": None, "sentiment": None,
        "score": None, "aspects": [], "themes": [], "analyser": None, "reply": None, "flags": flags, "error": None,
    }
    try:
        text = extract_text(item, client, flags).strip()
        language, script, english, source = resolve_language(item, text, client, options, flags)
        analysis = analyser.analyse(english)
    except (BodhanError, OSError, ValueError, wave.Error) as problem:
        record["error"] = str(problem)
        return record

    clean = (lambda value: value) if options.keep_pii else mask_pii
    record.update(
        original=clean(text), language=language, language_source=source, script=script, english=clean(english),
        sentiment=analysis["sentiment"], score=analysis["score"], aspects=analysis["aspects"],
        themes=[aspect["theme"] for aspect in analysis["aspects"]], analyser=analysis["analyser"],
    )
    if not needs_reply(analysis):
        return record

    theme = worst_theme(analysis)
    template = taxonomy["themes"][theme]["reply"] if theme else taxonomy["default_reply"]
    try:
        reply = draft_reply(client, template, language, script)
        reply["theme"] = theme
        if options.speak == "all" or (options.speak == "voice" and item.channel == "voice"):
            audio = voice_reply(client, reply)
            if audio:
                target = options.out_dir / "replies" / f"{UNSAFE.sub('_', item.id)}.wav"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(audio)
                reply["audio"] = f"replies/{target.name}"
        record["reply"] = reply
    except (BodhanError, OSError, ValueError, wave.Error) as problem:
        flags.append(f"reply could not be drafted: {problem}")
    return record


def run(items: list[Item], client, analyser, taxonomy: dict, options: Options, progress=sys.stderr) -> list[dict]:
    done = 0
    lock = threading.Lock()

    def work(item: Item) -> dict:
        nonlocal done
        record = process_item(item, client, analyser, taxonomy, options)
        with lock:
            done += 1
            position = done
        if progress:
            status = "error: " + record["error"] if record["error"] else f"{record['language']} {record['sentiment']}"
            print(f"[{position}/{len(items)}] {item.id} ({item.channel}) {status}", file=progress)
        return record

    with ThreadPoolExecutor(max_workers=max(1, options.workers)) as pool:
        return list(pool.map(work, items))
