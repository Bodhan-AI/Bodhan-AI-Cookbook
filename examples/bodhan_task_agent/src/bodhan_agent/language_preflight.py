"""Conservative target-language check for explicitly requested outputs.

This does not special-case any demo task or target language. The existing
Bodhan language registry remains the single source of supported languages.
When a target is not explicit, the normal planner and typed tools decide.
"""
from __future__ import annotations

import re

from bodhan_shared.languages import normalize_lang

_OUTPUT_TARGET = re.compile(
    r"\b(?:translate|translation|translated|dub|narrate|narration|speak|speech|"
    r"synthesize|synthesis|read|subtitles?)\b"
    r"[^\n.!?]{0,150}?\b(?:into|to|in)\s+([A-Za-z][A-Za-z-]*)\b",
    re.IGNORECASE,
)

# Format/determiner words may follow a preposition but are not languages.
_NOT_LANGUAGE = {
    "a", "an", "the", "markdown", "md", "text", "file", "document",
    "json", "audio", "video", "mp3", "mp4", "wav", "srt", "speech",
    "subtitles", "plain", "another", "any", "one", "language", "some", "format",
}


def unsupported_target_language(task: str) -> str | None:
    """Return an explicit unsupported target name, or None if uncertain.

    This is only a safe early rejection for well-formed target-language
    phrases. Ambiguous requests still proceed to the LLM and typed tool APIs.
    """
    for match in _OUTPUT_TARGET.finditer(task):
        target = match.group(1)
        if target.casefold() in _NOT_LANGUAGE:
            continue
        try:
            normalize_lang(target)
        except ValueError:
            return target
    return None
