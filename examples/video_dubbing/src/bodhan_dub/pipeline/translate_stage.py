"""Stage: translate each segment's English into the target language.

All target-language text comes from Indic-Translate — never the LLM. ``--code-mixed``
selects the ``codemix`` script (e.g. Hinglish for tech talks). Segments are
translated concurrently; the duration-fit stage may later re-translate a condensed
English variant of a segment, which is why translate() is a thin, reusable call.
"""

from __future__ import annotations

import asyncio

from ..config import SUPPORTED_LANGS
from ..logging_setup import get_logger
from ..types import DubSegment, Segment

log = get_logger("translate")


def target_token(lang: str) -> str:
    return SUPPORTED_LANGS.get(lang, {}).get("translate", lang)


async def translate_one(api, text: str, lang: str, script: str, cache=None) -> tuple[str, int]:
    key = cache.key("mt", f"{lang}|{script}|{text}".encode()) if cache else None
    if cache and (hit := cache.get_json(key)) is not None:
        return hit["t"], hit["tok"]
    translation, tokens = await api.translate(text, target_token(lang), script)
    if cache:
        cache.set_json(key, {"t": translation, "tok": tokens})
    return translation, tokens


async def translate_segments(
    api, segments: list[Segment], lang: str, *, code_mixed: bool, cache=None
) -> list[DubSegment]:
    script = "codemix" if code_mixed else "native"
    dubs: list[DubSegment] = []
    for seg in segments:
        dubs.append(DubSegment(index=seg.index, start=seg.start, end=seg.end,
                               source_text=seg.text, script=script, speaker=seg.speaker))

    async def one(dub: DubSegment):
        if not dub.source_text.strip():
            dub.status, dub.reason = "skipped", "no_speech"
            return
        dub.translation, _ = await translate_one(api, dub.source_text, lang, script, cache)

    await asyncio.gather(*(one(d) for d in dubs))
    log.info("translated", extra={"lang": lang, "event": "translated",
             "extra": {"segments": sum(1 for d in dubs if d.translation)}})
    return dubs
