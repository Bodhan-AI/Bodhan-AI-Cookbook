"""Shared pieces of the injector: the ``Edit`` a method returns, the context it reads, and small text helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..clients import BodhanClient
from ..models import Item
from ..normalize import SENTENCE_END, segments, word_tokens

Span = tuple[int, int]


@dataclass
class Edit:
    """What one method did to one item.

    ``span`` locates the error in the new translation (``span_on="translation"``) or in the English source
    (``span_on="source"``, used when a whole aligned sentence went missing). A zero-width span marks the place
    where something is missing.
    """

    span: Span
    span_on: str = "translation"
    translation: str | None = None  # the new translation, when the text changed
    audio: np.ndarray | None = None  # the new waveform, when the audio changed
    sr: int = 0
    note: str = ""


@dataclass
class InjectContext:
    """Read-only inputs shared by all methods.

    ``bodhan`` is set only with ``--with-models`` and is the only model client the injector ever holds: there is
    no LLM client here, by design (CLAUDE.md §4.4).
    """

    items: list[Item]
    bodhan: BodhanClient | None = None
    audio_out: Path | None = None
    voices: dict[str, str] = field(default_factory=dict)
    by_lang: dict[str, list[Item]] = field(init=False)
    by_id: dict[str, list[Item]] = field(init=False)

    def __post_init__(self) -> None:
        self.by_lang, self.by_id = {}, {}
        for it in sorted(self.items, key=lambda i: i.key):  # sorted: random choices from pools are reproducible
            self.by_lang.setdefault(it.lang, []).append(it)
            self.by_id.setdefault(it.id, []).append(it)

    @property
    def options(self) -> dict:
        """What ``generate.synthesize`` reads from a run context (the chosen voices)."""
        return {"voices": self.voices}

    def others(self, item: Item) -> list[Item]:
        """Other items in the same language that have a translation."""
        return [o for o in self.by_lang.get(item.lang, []) if o.id != item.id and o.translation]


# ---------------------------------------------------------------------------
# text helpers
# ---------------------------------------------------------------------------

CLOSERS = ".,;:!?।॥)]\"'”’"
MARKUP_CHARS = frozenset("|*`$\\#")
LINE_MARK = re.compile(r"^\s*(?:[-*+>]|\d+[.)])\s")


def prose_segments(text: str, min_words: int = 3) -> list[Span]:
    """Sentences of running text with at least ``min_words`` words.

    Table rows, headings, list items and anything with Markdown or LaTeX are left out, so a prose edit never
    also breaks the markup (that would be a second, unlabelled error).
    """
    out = []
    for a, b in segments(text):
        seg = text[a:b]
        if MARKUP_CHARS & set(seg) or LINE_MARK.match(text[text.rfind("\n", 0, a) + 1 : b]):
            continue
        if len(word_tokens(seg)) >= min_words:
            out.append((a, b))
    return out


def aligned(item: Item) -> list[tuple[Span, Span]] | None:
    """(source sentence, translation sentence) pairs, when both texts split into the same number of sentences.

    Then the i-th sentences correspond. This cheap alignment is right for most educational text, and the
    injector only uses it to place a label on the source or to swap one sentence; with unequal counts the
    methods that need it do not apply.
    """
    s, t = segments(item.source), segments(item.translation or "")
    return list(zip(s, t)) if s and len(s) == len(t) else None


def remove(text: str, a: int, b: int) -> tuple[str, int]:
    """Delete ``text[a:b]`` and one joining space; returns the new text and the position of the gap."""
    if a > 0 and text[a - 1] == " " and (b == len(text) or text[b] in " \n" or text[b] in CLOSERS):
        a -= 1
    elif b < len(text) and text[b] == " " and (a == 0 or text[a - 1] == "\n"):
        b += 1
    return text[:a] + text[b:], a


def insert_after(text: str, pos: int, new: str) -> tuple[str, Span]:
    """Insert ``new`` at ``pos`` (the end of a sentence), joined by one space; returns the text and its span."""
    return f"{text[:pos]} {new}{text[pos:]}", (pos + 1, pos + 1 + len(new))


def replace(text: str, a: int, b: int, new: str) -> tuple[str, Span]:
    return text[:a] + new + text[b:], (a, a + len(new))


def sentence_body_end(sentence: str) -> int:
    """Index where the sentence's closing punctuation starts (its length if there is none)."""
    return len(sentence.rstrip(SENTENCE_END + "\"'”’)"))
