"""Speech checks (plan §12): signal checks on the audio, and a back-transcription compared with the text.

Signal (free, audio.py): unreadable audio, long silences, clipping, noise. Thresholds sit well
clear of clean Indic-Speak audio (measured on 7 clips: no leading/trailing silence, pauses <= 0.25 s,
peak <= 0.45, flatness smoothed over 0.5 s <= 0.073; white noise mixed in at 0 dB gives 0.31-0.52).

Back-transcription (Indic-Transcribe, <= 30 s per request): the audio is cut at pauses into pieces of
<= 25 s, each transcribed (cached by its bytes). The transcript is aligned word by word with what
was spoken (speakable(translation)), both loosely normalized. Words with digits or Latin letters are
wildcards that match 1..WILDCARD_MAX transcript words for free: the transcriber writes "25" as words,
often as English number words in the Indic script. Substitution costs 1 - character similarity, so a
near-miss spelling is almost a match.

Checker noise vs system errors (§12.4): only contiguous runs flag, never the overall word error rate:
- speech_omission: consecutive spoken words missing from the transcript, >= MIN_MISSING_CHARS of
  them (characters, not words: a Tamil or Bengali word can take a second to say); "truncation"
  when the run reaches the end;
- speech_repetition: extra transcript words (>= MIN_REPEAT_CHARS) repeating the words right before
  or after them — transcribers drop words, they rarely say one twice;
- speech_garbled: a window of GARBLE_WINDOW words matching badly, extra words that repeat nothing,
  or no words recognised in audio that has sound.
No timestamps come back from the API, so a transcript word's time is estimated from its position in
its piece (ponytail: proportional; exact with word timestamps if the API ever gives them).

Second listen (the speech counterpart of the back-translation noise control): the audio around
each suspected omission is transcribed again from a different cut (SECOND_LISTEN_S either side).
If the "missing" words are heard there, the transcriber missed them and the finding is weak; if
not, they weren't spoken, and it is strong. This, not a fixed noise floor, decides, so it carries
to languages we never measured.

Transcriber failures (seen live: a 20 s piece transcribed as two words and then one character
repeated hundreds of times; a 21 s piece as one word) would read as long omissions. Two guards:
a degenerate piece (< MIN_WORDS_PER_S, or a character repeated 8+ times) is transcribed again in
shorter pieces cut at pauses; and the pipeline (speech_consistency) downgrades an omission whose
share of missing words is far larger than the audio's shortfall against the language's speaking rate.
"""

from __future__ import annotations

import difflib
import hashlib
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import audio as A
from .checks import Candidate
from .normalize import normalize
from .segment import speakable

SPEECH_CATEGORIES = ("speech_omission", "speech_repetition", "speech_garbled")
EDGE_SILENCE_S = 1.0
LONG_SILENCE_S = 1.0
STRONG_SILENCE_S = 1.5  # clean Indic-Speak pauses are <= 0.25 s
CLIP_FRACTION = 0.001
NOISE_LEVEL = 0.15  # smoothed flatness (audio.noise_level)
NOISE_S = 0.3
MIN_MISSING_CHARS = 10  # ~0.7 s of speech at Indic-Speak's 13-15 chars/s
# ponytail: the transcriber's noise floor, measured on 31 clean clips (hi/ta/bn): the longest run of
# "missing" characters was 10. Strong just above it; a per-language floor learnt from the run's own
# clips would adapt to new languages.
STRONG_MISSING_CHARS = 12
MIN_REPEAT_CHARS = 4
STRONG_REPEAT_CHARS = 8
GARBLE_WINDOW = 6
GARBLE_COST = 0.6
NEAR_MISS = 0.34  # a substitution this cheap counts as the same word
WILDCARD_MAX = 8
SECOND_LISTEN_S = 2.5
DIGIT_CHARS = 6  # a digit takes about this many characters to say ("458" -> "four hundred and fifty eight")
MIN_WORDS_PER_S = 0.8
RETRY_MAX_S = 8.0
CHARS_PER_SECOND = 10.0  # Indic-Speak's measured rate; the per-language median replaces it with >= 5 clips
RATE_LOW, RATE_HIGH = 0.6, 1.6  # actual / expected duration


def check_speech(translation: str, path: Path, lang: str, clients, piece_dir: Path) -> tuple[list[Candidate], dict, dict]:
    """(candidates, info for the record, check statuses)."""
    try:
        audio = A.load(path)
    except (ValueError, OSError) as e:
        c = Candidate("speech_signal", "speech_garbled", "translation", (0, len(translation)), 1.0,
                      {"kind": "unreadable", "error": str(e)}, "critical", standalone=True)
        return [c], {"audio": str(path)}, {"speech_signal": "ok", "speech_asr": "skipped: unreadable audio"}
    info = {"audio": str(path), "duration_s": round(audio.duration, 3)}
    out = signal_candidates(audio, translation)
    statuses = {"speech_signal": "ok"}
    if not clients.available("transcribe"):
        statuses["speech_asr"] = "skipped: BODHAN_TRANSCRIBE_KEY not set"
        return out, info, statuses
    pieces, retried = [], 0
    for s, e in A.pieces(audio):
        text = _transcribe(audio, s, e, lang, clients, piece_dir)  # BudgetExceeded/ModelError go up
        if not _degenerate(text, e - s):
            pieces.append((s, e, text))
            continue
        retried += 1
        part = A.cut(audio, s, e)
        for s2, e2 in A.pieces(part, max_s=RETRY_MAX_S, min_s=3.0, slack=0.0):  # must cut, or the retry is a cache hit
            pieces.append((s + s2, s + e2, _transcribe(audio, s + s2, s + e2, lang, clients, piece_dir)))
    info["pieces"] = [{"start_s": round(s, 2), "end_s": round(e, 2), "transcript": t} for s, e, t in pieces]
    if retried:
        info["retried_pieces"] = retried
    info["transcript"] = " ".join(t for _, _, t in pieces)
    found = asr_candidates(translation, pieces, audio)
    for c in found:
        if c.check == "speech_asr" and c.category == "speech_omission" and c.evidence["kind"] in ("omission", "truncation"):
            _second_listen(c, audio, lang, clients, piece_dir)
    out += found
    statuses["speech_asr"] = "ok"
    return out, info, statuses


def _transcribe(audio: A.Audio, s: float, e: float, lang: str, clients, piece_dir: Path) -> str:
    part = A.cut(audio, s, e)
    pcm = (np.clip(part.samples, -1, 1) * 32767).round().astype("<i2").tobytes()
    file = A.save(part, piece_dir / f"{hashlib.sha256(pcm).hexdigest()[:24]}.wav")
    return clients.transcribe(file, lang.split("-")[0])


def _second_listen(c: Candidate, audio: A.Audio, lang: str, clients, piece_dir: Path) -> None:
    t0, t1 = c.evidence["time"]
    s, e = max(0.0, t0 - SECOND_LISTEN_S), min(audio.duration, t1 + SECOND_LISTEN_S)
    heard = [t for t, _ in _tokens(_transcribe(audio, s, e, lang, clients, piece_dir))]
    missing = c.evidence["missing_text"].split()
    chars = sum(len(m) for m in missing) or 1
    share = sum(len(m) for m in missing if any(_cost(m, h) <= NEAR_MISS for h in heard)) / chars
    c.evidence["second_listen"] = {"window": [round(s, 2), round(e, 2)], "heard_share": round(share, 2)}
    if share >= 0.5:
        c.standalone = False
        c.evidence["likely"] = "transcriber miss: the words were heard on a second listen"
    else:
        c.standalone = c.evidence["missing_chars"] >= MIN_MISSING_CHARS


def spoken_length(text: str) -> int:
    """Characters it takes to say the text: digits are said as words."""
    spoken = speakable(text)
    return len(spoken) + (DIGIT_CHARS - 1) * sum(ch.isdigit() for ch in spoken)


def _degenerate(text: str, seconds: float) -> bool:
    """A transcript that can't be right for this much speech."""
    return (seconds >= 3 and len(text.split()) / seconds < MIN_WORDS_PER_S) or bool(re.search(r"(.)\1{7,}", text))


def consistency(cands: list[dict], ratio: float) -> None:
    """Downgrade omissions the audio length contradicts: if 40% of the words are missing from the
    transcript but the audio is as long as the full text needs, the transcriber missed them."""
    for c in cands:
        if c["check"] == "speech_asr" and c["category"] == "speech_omission" \
                and c["evidence"].get("share", 0) - max(0.0, 1 - ratio) > 0.35:  # speaking rate varies ~±20% per item
            c["standalone"] = False
            c["evidence"]["audio_length_ratio"] = round(ratio, 2)
            c["evidence"]["likely"] = "transcriber miss: the audio is about as long as the full text needs"


# --- signal ---------------------------------------------------------------------------------------------

def _span(t0: float, t1: float, duration: float, n: int) -> tuple[int, int]:
    """Text span for a time span, by proportion (no timestamps)."""
    d = max(duration, 1e-9)
    return (min(n, int(n * t0 / d)), min(n, int(round(n * t1 / d))))


def signal_candidates(audio: A.Audio, text: str) -> list[Candidate]:
    n, d, out = len(text), audio.duration, []

    def add(kind, t0, t1, severity, strong, **ev):
        out.append(Candidate("speech_signal", "speech_garbled", "translation", _span(t0, t1, d, n), 1.0 if strong else 0.6,
                             {"kind": kind, "time": [round(t0, 2), round(t1, 2)], **ev}, severity, standalone=strong))

    if d == 0 or A.silent_frames(audio).all():
        out.append(Candidate("speech_signal", "speech_omission", "translation", (0, n), 1.0,
                             {"kind": "silent_audio", "time": [0.0, round(d, 2)]}, "critical", standalone=True))
        return out
    for s, e in A.silences(audio, min(EDGE_SILENCE_S, LONG_SILENCE_S)):
        edge = s <= A.FRAME_S or e >= d - 2 * A.FRAME_S
        long = e - s
        if edge and long >= EDGE_SILENCE_S:
            add("leading_silence" if s <= A.FRAME_S else "trailing_silence", s, e, "minor", False, seconds=round(long, 2))
        elif not edge and long >= LONG_SILENCE_S:
            add("silence", s, e, "major", long >= STRONG_SILENCE_S, seconds=round(long, 2))
    clip = A.clipped_fraction(audio)
    if clip > CLIP_FRACTION:
        add("clipping", 0.0, d, "major", True, clipped_share=round(clip, 4))
    noisy = A.noise_level(audio) > NOISE_LEVEL
    for s, e in A.runs(noisy):
        if (e - s) * A.FRAME_S >= NOISE_S:
            add("noise", s * A.FRAME_S, e * A.FRAME_S, "major", True, seconds=round((e - s) * A.FRAME_S, 2))
    return out


def rate_candidate(text: str, duration: float, chars_per_second: float) -> Candidate | None:
    """Audio much shorter or longer than its text needs at this language's speaking rate."""
    expected = spoken_length(text) / chars_per_second
    if expected <= 0:
        return None
    ratio = duration / expected
    if RATE_LOW <= ratio <= RATE_HIGH:
        return None
    return Candidate("speech_rate", "speech_omission" if ratio < RATE_LOW else "speech_repetition", "translation",
                     (0, len(text)), 0.6, {"kind": "short_audio" if ratio < RATE_LOW else "long_audio",
                                           "ratio": round(ratio, 2), "expected_s": round(expected, 1),
                                           "duration_s": round(duration, 2), "chars_per_second": round(chars_per_second, 2)},
                     "major")


# --- back-transcription ---------------------------------------------------------------------------------

def _tokens(text: str):
    """Loosely normalized words with their span in `text`."""
    norm = normalize(text, loose=True)
    return [(m.group(), norm.to_original(m.start(), m.end())) for m in re.finditer(r"\S+", norm.text)]


def reference(translation: str) -> list[tuple[str, tuple[int, int] | None]]:
    """The spoken words (speakable text), each with its span in the translation (None if not found)."""
    spoken, out, cursor = speakable(translation), [], 0
    for tok, (s, e) in _tokens(spoken):
        word = spoken[s:e]
        at = translation.find(word, cursor)
        if at < 0:
            out.append((tok, None))
        else:
            out.append((tok, (at, at + len(word))))
            cursor = at + len(word)
    return out


def _wild(tok: str) -> bool:
    return any(c.isdigit() or unicodedata.name(c, "").startswith("LATIN") for c in tok)


@lru_cache(maxsize=100_000)
def _cost(a: str, b: str) -> float:
    return 0.0 if a == b else 1.0 - difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def align(ref: list[str], hyp: list[str]) -> list[tuple[str, int | None, list[int], float]]:
    """Edit script: (op, ref index, hyp indices, cost); op in match / sub / wild / del / ins."""
    n, m = len(ref), len(hyp)
    inf = float("inf")
    D = [[inf] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    D[0][0] = 0.0
    for i in range(n + 1):
        for j in range(m + 1):
            if i == j == 0:
                continue
            best, how = inf, None
            if i and D[i - 1][j] + 1 < best:
                best, how = D[i - 1][j] + 1, ("del", 1, 0)
            if j and D[i][j - 1] + 1 < best:
                best, how = D[i][j - 1] + 1, ("ins", 0, 1)
            if i and j:
                c = _cost(ref[i - 1], hyp[j - 1])
                if D[i - 1][j - 1] + c < best:
                    best, how = D[i - 1][j - 1] + c, ("match" if c <= NEAR_MISS else "sub", 1, 1)
                if _wild(ref[i - 1]):
                    digits = sum(ch.isdigit() for ch in ref[i - 1])
                    for k in range(1, min(max(WILDCARD_MAX, 3 * digits), j) + 1):
                        if D[i - 1][j - k] < best:
                            best, how = D[i - 1][j - k], ("wild", 1, k)
            D[i][j], back[i][j] = best, how
    ops, i, j = [], n, m
    while i or j:
        op, di, dj = back[i][j]
        cost = D[i][j] - D[i - di][j - dj]
        ops.append((op, i - 1 if di else None, list(range(j - dj, j)), round(cost, 3)))
        i, j = i - di, j - dj
    return ops[::-1]


def asr_candidates(translation: str, pieces: list[tuple[float, float, str]], audio: A.Audio) -> list[Candidate]:
    ref = reference(translation)
    hyp, times = [], []
    for s, e, text in pieces:
        words = [t for t, _ in _tokens(text)]
        for k, w in enumerate(words):
            hyp.append(w)
            times.append(s + (e - s) * (k + 0.5) / len(words))
    n_text, d = len(translation), audio.duration
    if not ref:
        return []
    if not hyp:
        return [Candidate("speech_asr", "speech_garbled", "translation", (0, n_text), 1.0,
                          {"kind": "nothing_recognised", "time": [0.0, round(d, 2)]}, "critical", standalone=True)]
    ops = align([t for t, _ in ref], hyp)
    out: list[Candidate] = []

    def text_span(ri: list[int]) -> tuple[int, int]:
        spans = [ref[i][1] for i in ri if ref[i][1] is not None]
        return (spans[0][0], spans[-1][1]) if spans else (0, n_text)

    def hyp_time(k: int, after: bool) -> float:
        """Time of the aligned transcript word nearest op k, looking back (after=False) or forward."""
        rng = range(k + 1, len(ops)) if after else range(k - 1, -1, -1)
        for q in rng:
            if ops[q][2]:
                return times[ops[q][2][0] if after else ops[q][2][-1]]
        return d if after else 0.0

    covered: set[int] = set()
    k = 0
    while k < len(ops):  # runs of missing words
        if ops[k][0] != "del":
            k += 1
            continue
        start = k
        while k < len(ops) and ops[k][0] == "del":
            k += 1
        ri = [ops[q][1] for q in range(start, k)]
        chars = sum(len(ref[i][0]) for i in ri if not _wild(ref[i][0]))  # a dropped number word is the wildcard's doing
        if chars >= MIN_MISSING_CHARS:
            tail = all(op in ("del", "ins") for op, *_ in ops[k:])
            share = len(ri) / len(ref)
            t0, t1 = hyp_time(start, False), hyp_time(k - 1, True)
            out.append(Candidate("speech_asr", "speech_omission", "translation", text_span(ri), 0.8,
                                 {"kind": "truncation" if tail else "omission", "missing_words": len(ri),
                                  "missing_chars": chars, "missing_text": " ".join(ref[i][0] for i in ri if not _wild(ref[i][0])),
                                  "share": round(share, 2), "time": [round(t0, 2), round(t1, 2)]},
                                 "critical" if share >= 0.5 else "major",
                                 standalone=chars >= (MIN_MISSING_CHARS if tail else STRONG_MISSING_CHARS)))
            covered.update(ri)

    k = 0
    while k < len(ops):  # runs of extra words
        if ops[k][0] != "ins":
            k += 1
            continue
        start = k
        while k < len(ops) and ops[k][0] == "ins":
            k += 1
        hj = [ops[q][2][0] for q in range(start, k)]
        before = hyp[max(0, hj[0] - len(hj)):hj[0]]
        after = hyp[hj[-1] + 1:hj[-1] + 1 + len(hj)]
        extra = [hyp[j] for j in hj]
        chars = sum(len(w) for w in extra)
        repeats = chars >= MIN_REPEAT_CHARS and any(
            len(x) == len(extra) and np.mean([1 - _cost(a, b) for a, b in zip(x, extra)]) >= 0.8 for x in (before, after))
        near = [ops[q][1] for q in range(max(0, start - len(hj)), start) if ops[q][1] is not None] or \
               [ops[q][1] for q in range(k, min(len(ops), k + len(hj))) if ops[q][1] is not None]
        if repeats or len(hj) >= 4:
            out.append(Candidate("speech_asr", "speech_repetition" if repeats else "speech_garbled", "translation",
                                 text_span(near) if near else (0, n_text), 0.8 if repeats else 0.5,
                                 {"kind": "repeated_words" if repeats else "extra_words", "extra_words": len(hj),
                                  "heard": " ".join(extra)[:120], "time": [round(times[hj[0]], 2), round(times[hj[-1]], 2)]},
                                 "major", standalone=repeats and chars >= STRONG_REPEAT_CHARS))

    per_ref = {}  # cost per spoken word: substitutions and deletions
    for q, (op, i, _, cost) in enumerate(ops):
        if i is not None:
            per_ref[i] = (cost, q)
    bad = [False] * len(ref)
    for s in range(0, max(0, len(ref) - GARBLE_WINDOW) + 1):
        window = [i for i in range(s, s + GARBLE_WINDOW) if i < len(ref)]
        if len(window) == GARBLE_WINDOW and not covered & set(window) \
                and np.mean([per_ref.get(i, (1.0, 0))[0] for i in window]) > GARBLE_COST:
            for i in window:
                bad[i] = True
    for s, e in A.runs(np.array(bad)):
        ri = list(range(s, e))
        q0, q1 = per_ref[ri[0]][1], per_ref[ri[-1]][1]
        out.append(Candidate("speech_asr", "speech_garbled", "translation", text_span(ri), 0.5,
                             {"kind": "unintelligible", "words": len(ri),
                              "time": [round(hyp_time(q0, False), 2), round(hyp_time(q1, True), 2)]}, "major"))
    return out
