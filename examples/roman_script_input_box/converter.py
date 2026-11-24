"""Word-by-word and whole-sentence conversion on top of bodhan_client.

* word:     split into tokens, convert each roman word on its own (cached, in parallel),
            and put spaces, digits and punctuation back exactly where they were.
* sentence: send the whole line in one call, so the model sees context.

The app's hybrid mode shows a cache-only ``word`` draft while you type (no API calls)
and runs ``sentence`` when you pause or press Send. Each sentence answer that lines up
word for word with the input is added to the word cache, so drafts improve as you use
it. See README, "Word, sentence or hybrid?".
"""

from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from bodhan_client import Client

# A "word" is a run of ASCII letters (plus inner apostrophes: "don't"). Everything else,
# including text already in an Indic script, is passed through untouched.
WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)*")
MAX_PARALLEL = 4   # the key allows 10 in flight; leave room for the sentence call


def tokenize(text: str) -> list[tuple[str, bool]]:
    """Split text into (piece, is_word) pairs whose pieces join back to the input."""
    out, pos = [], 0
    for m in WORD.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], False))
        out.append((m.group(), True))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], False))
    return out


class LRU:
    """A tiny thread-safe LRU cache. Typing repeats words a lot, so this saves real calls."""

    def __init__(self, size: int = 2000):
        self.size, self._d, self._lock = size, OrderedDict(), threading.Lock()

    def get(self, key):
        with self._lock:
            if key in self._d:
                self._d.move_to_end(key)
                return self._d[key]
            return None

    def put(self, key, value) -> None:
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self.size:
                self._d.popitem(last=False)


@dataclass
class Conversion:
    text: str
    mode: str
    latency_ms: float      # wall-clock time for the whole conversion
    calls: int = 0         # API calls actually made
    cached: int = 0        # words answered from the cache
    truncated: bool = False
    words: list[dict] = field(default_factory=list)


class Converter:
    def __init__(self, client: Client, cache: LRU | None = None):
        self.client = client
        self.cache = cache or LRU()
        self._pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL)

    def sentence(self, text: str, language: str, script: str = "native") -> Conversion:
        r = self.client.transliterate(text, language, script)
        calls = 1 if text.strip() else 0
        if script == "native":
            self._seed_cache(text, r.text, language)
        return Conversion(r.text, "sentence", r.latency_ms, calls=calls, truncated=r.truncated)

    def _seed_cache(self, roman: str, native: str, language: str) -> None:
        """If the input is only words and spaces and the answer has the same number of
        words, pair them up. Words learnt in context beat words asked one at a time."""
        pieces = tokenize(roman)
        if any(not is_word and piece.strip() for piece, is_word in pieces):
            return
        words = [p.lower() for p, is_word in pieces if is_word]
        out = native.split()
        if len(words) == len(out):
            for w, n in zip(words, out, strict=True):
                self.cache.put((language, "native", w), n)

    def word(self, text: str, language: str, script: str = "native", cache_only: bool = False) -> Conversion:
        """Convert word by word. With ``cache_only``, unknown words stay in roman and no
        API call is made (the app's typing draft)."""
        started = time.perf_counter()
        pieces = tokenize(text) if script == "native" else [(p, bool(p.strip())) for p in re.split(r"(\s+)", text) if p]
        missing = sorted({p.lower() for p, is_word in pieces if is_word and self.cache.get((language, script, p.lower())) is None})
        todo = [] if cache_only else missing

        def one(w: str):
            return w, self.client.transliterate(w, language, script)

        calls = 0
        for w, r in self._pool.map(one, todo):
            self.cache.put((language, script, w), r.text)
            calls += 1

        out, words, cached = [], [], 0
        for piece, is_word in pieces:
            if not is_word:
                out.append(piece)
                continue
            native = self.cache.get((language, script, piece.lower())) or piece
            if piece.lower() not in missing:
                cached += 1
            out.append(native)
            words.append({"roman": piece, "native": native})
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        return Conversion("".join(out), "draft" if cache_only else "word", elapsed, calls=calls, cached=cached, words=words)

    def convert(self, text: str, language: str, mode: str, script: str = "native") -> Conversion:
        if mode == "word":
            return self.word(text, language, script)
        if mode == "draft":
            return self.word(text, language, script, cache_only=True)
        if mode == "sentence":
            return self.sentence(text, language, script)
        raise ValueError("mode must be 'word', 'draft' or 'sentence'")
