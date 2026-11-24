"""Word-by-word and whole-sentence conversion on top of bodhan_client.

* word:     split into tokens, convert each roman word on its own (cached, in parallel),
            and put spaces, digits and punctuation back exactly where they were.
* draft:    the same, but cache only: no API calls, unknown words stay in roman.
* sentence: send the whole line in one call, so the model sees context.

The app's hybrid mode shows a ``draft`` while you type and runs ``sentence`` when you
pause or press Send. Each sentence answer that lines up word for word with the input
is added to the word cache, so drafts improve as you use it. See README,
"Word, sentence or hybrid?".
"""

from __future__ import annotations

import re
import threading
import time
import unicodedata
from collections import OrderedDict
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from dataclasses import dataclass, field

from bodhan_client import BodhanError, Client

# A "word" is a run of ASCII letters (plus inner apostrophes: "don't"). Everything else,
# including text already in an Indic script, is passed through untouched.
WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)*")
MAX_PARALLEL = 4     # the key allows 10 in flight; leave room for the sentence call
MAX_NEW_WORDS = 20   # API calls one word-mode request may make (the key's quota is ~120)
ZERO_WIDTH_JOINERS = {"\u200c", "\u200d"}   # ZWNJ, ZWJ

# The Unicode script each language is written in, as the first word of the characters'
# Unicode names. Used to keep wrong-script answers out of the cache.
SCRIPT_OF = {
    "hi": "DEVANAGARI", "mr": "DEVANAGARI", "ne": "DEVANAGARI", "sa": "DEVANAGARI", "mai": "DEVANAGARI",
    "kok": "DEVANAGARI", "doi": "DEVANAGARI", "brx": "DEVANAGARI", "sd": "DEVANAGARI", "ks-Deva": "DEVANAGARI",
    "bn": "BENGALI", "as": "BENGALI", "mni-Beng": "BENGALI", "gu": "GUJARATI", "pa": "GURMUKHI",
    "or": "ORIYA", "ta": "TAMIL", "te": "TELUGU", "kn": "KANNADA", "ml": "MALAYALAM",
    "ur": "ARABIC", "ks": "ARABIC", "sd-Arab": "ARABIC", "mni": "MEETEI", "sat": "OL",
}


class TooManyWords(ValueError):
    """A word-mode request would need more API calls than MAX_NEW_WORDS."""


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


def split_edges(chunk: str) -> tuple[str, str, str]:
    """Split punctuation off both ends: ``"hai,"`` → ``("", "hai", ",")``. Inner
    punctuation (``don't``) stays."""
    start, end = 0, len(chunk)
    while start < end and unicodedata.category(chunk[start]).startswith("P"):
        start += 1
    while end > start and unicodedata.category(chunk[end - 1]).startswith("P"):
        end -= 1
    return chunk[:start], chunk[start:end], chunk[end:]


def tokenize_native(text: str) -> list[tuple[str, bool]]:
    """For native → roman: words are runs of non-space characters, with punctuation at
    either end split off so it is kept (``नमस्ते,`` → ``नमस्ते`` + ``,``)."""
    out: list[tuple[str, bool]] = []
    for chunk in re.split(r"(\s+)", text):
        if not chunk:
            continue
        if chunk.isspace():
            out.append((chunk, False))
            continue
        head, core, tail = split_edges(chunk)
        for piece, is_word in ((head, False), (core, True), (tail, False)):
            if piece:
                out.append((piece, is_word))
    return out


def word_key(word: str, all_caps: bool = False) -> str:
    """Cache/API form of a typed word: lower case, except acronyms ("US" is not "us").
    ``all_caps`` means the whole message is in capitals (caps lock), so no word in it is
    treated as an acronym."""
    return word if not all_caps and len(word) > 1 and word.isupper() else word.lower()


def is_all_caps(text: str) -> bool:
    """True for caps-lock text with at least two words ("KAL MEETING HAI")."""
    words = WORD.findall(text)
    return len(words) > 1 and all(w.isupper() for w in words)


def strip_punct(word: str) -> str:
    """Drop punctuation the model adds around a word (e.g. a final danda: है।)."""
    return "".join(ch for ch in word if not unicodedata.category(ch).startswith("P"))


def in_script(word: str, language: str) -> bool:
    """True if every letter of ``word`` belongs to the script ``language`` is written in."""
    script = SCRIPT_OF.get(language)
    letters = [ch for ch in word if unicodedata.category(ch)[0] in "LM"]
    if not script or not letters:
        return False
    return all(unicodedata.name(ch, "").startswith(script) for ch in letters)


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

    def close(self) -> None:
        """Stop the worker threads (the app's Converter lives as long as the server)."""
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _remember(self, language: str, script: str, key: str, answer: str) -> None:
        """Cache one word under ``key`` (a ``word_key``), but only if the answer is plausibly
        right: for native script, a single word in the target script."""
        answer = split_edges(answer.strip())[1]
        if not answer:
            return
        if script == "native":
            # Letters, vowel signs and zero-width joiners (part of many Bengali, Urdu and
            # Malayalam words) only: no digits ("5पीएम"), no spaces, right script.
            if any(unicodedata.category(ch)[0] not in "LM" and ch not in ZERO_WIDTH_JOINERS for ch in answer) \
                    or not in_script(answer, language):
                return
        self.cache.put((language, script, key), answer)

    def sentence(self, text: str, language: str, script: str = "native") -> Conversion:
        r = self.client.transliterate(text, language, script)
        calls = 1 if text.strip() else 0
        if script == "native":
            self._seed_cache(text, r.text, language)
        return Conversion(r.text, "sentence", r.latency_ms, calls=calls, truncated=r.truncated)

    def _seed_cache(self, roman: str, native: str, language: str) -> None:
        """Pair input words with answer words when the counts line up. Punctuation at a
        token's edges is ignored and punctuation-only tokens ("...") are skipped. If any
        token is not a plain word ("5pm", "e-mail"), nothing is learnt, so pairs can't
        misalign. Each pair still has to pass ``_remember``'s checks. Words learnt in
        context beat words asked one at a time (see the eval)."""
        words = [core for core in (split_edges(t)[1] for t in roman.split()) if core]
        if not words or not all(WORD.fullmatch(w) for w in words):
            return
        out = [core for core in (split_edges(t)[1] for t in native.split()) if core]
        if len(words) == len(out):
            caps = is_all_caps(roman)
            for w, n in zip(words, out, strict=True):
                self._remember(language, "native", word_key(w, caps), n)

    def word(self, text: str, language: str, script: str = "native", cache_only: bool = False) -> Conversion:
        """Convert word by word. With ``cache_only``, unknown words stay in roman and no
        API call is made (the app's typing draft)."""
        started = time.perf_counter()
        pieces = tokenize(text) if script == "native" else tokenize_native(text)
        caps = script == "native" and is_all_caps(text)
        missing = sorted({word_key(p, caps) for p, is_word in pieces
                          if is_word and self.cache.get((language, script, word_key(p, caps))) is None})
        todo = [] if cache_only else missing
        if len(todo) > MAX_NEW_WORDS:
            raise TooManyWords(f"{len(todo)} new words; word mode converts at most {MAX_NEW_WORDS} per message")

        answers: dict[str, str] = {}
        if todo:
            futures = {self._pool.submit(self.client.transliterate, w, language, script): w for w in todo}
            _, pending = wait(futures, return_when=FIRST_EXCEPTION)
            for f in pending:          # one word failed (e.g. 429): don't start any more calls,
                f.cancel()             # but keep the answers of calls already in flight
            done = wait([f for f in futures if not f.cancelled()]).done
            for f in done:
                if f.exception() is None:
                    answers[futures[f]] = f.result().text
                    self._remember(language, script, futures[f], f.result().text)
            for f in done:
                exc = f.exception()
                # An empty answer for one word just leaves that word in roman; anything
                # else (429, network) fails the message.
                if exc is not None and not (isinstance(exc, BodhanError) and exc.code == "empty_answer"):
                    raise exc

        out, words, cached = [], [], 0
        for piece, is_word in pieces:
            if not is_word:
                out.append(piece)
                continue
            key = word_key(piece, caps)
            # Prefer the cache (validated); fall back to this call's raw answer, then roman.
            native = self.cache.get((language, script, key)) or split_edges(answers.get(key, "").strip())[1] or piece
            if key not in missing:
                cached += 1
            out.append(native)
            words.append({"roman": piece, "native": native})
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        return Conversion("".join(out), "draft" if cache_only else "word", elapsed,
                          calls=len(answers), cached=cached, words=words)

    def convert(self, text: str, language: str, mode: str, script: str = "native") -> Conversion:
        if mode == "word":
            return self.word(text, language, script)
        if mode == "draft":
            return self.word(text, language, script, cache_only=True)
        if mode == "sentence":
            return self.sentence(text, language, script)
        raise ValueError("mode must be 'word', 'draft' or 'sentence'")
