"""Speech check: transcribe the audio (back-transcription) and compare with the text that
should have been spoken.

What counts as evidence (ASR is noisy, so single scattered substitutions never do):
  * a contiguous run of deleted text tokens           -> speech_omission
    ...reaching the end of the text                   -> speech_omission (truncation)
  * an n-gram repeated in the transcript but not in
    the text, made of inserted tokens                 -> speech_repetition
  * a window of mostly wrong tokens                   -> speech_garbled
    (low confidence alone; high when the waveform check found noise / silence there)

Numbers and words in a script other than the text's main one are "soft": TTS reads "2023"
as words and ASR may write "ISRO" in Devanagari, and neither is a speech error.

API limits handled here: Indic-Speak takes about 30 s of speech per request (text is chunked
to ~25 s), Indic-Transcribe takes at most 30 s of audio (long audio is cut at silences).
"""

from __future__ import annotations

import asyncio
import io
import re
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import soundfile as sf

from bodhan_eval.align import Op, align_tokens, repeated_ngrams
from bodhan_eval.checks.audio import frame_features, load_audio, spoken_chars
from bodhan_eval.checks.base import CheckResult, Flag
from bodhan_eval.languages import Language
from bodhan_eval.normalize import (
    Token,
    dominant_script,
    script_of,
    spans_overlap,
    split_sentences,
    tokenize,
)
from bodhan_eval.params import Params

NAME = "speech"


class SpeechClient(Protocol):
    async def speak(self, text: str, lang: str, voice: str, style: str | None = ...): ...
    async def transcribe(self, wav: bytes, language: str): ...


@dataclass(frozen=True)
class SpeechSegment:
    """A piece of audio and the text span it was meant to say (None = the whole text)."""

    wav: bytes
    text_span: tuple[int, int] | None = None


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

_CLAUSE = re.compile(r"[,;:،]\s+|\s+")


def chunk_text(text: str, chars_per_second: float, max_s: float = 25.0) -> list[tuple[int, int]]:
    """Spans of `text`, each estimated to take at most `max_s` seconds to speak.

    Packs whole sentences greedily; a sentence that is too long on its own is cut at the
    last clause mark (or space) that fits.
    """
    budget = max(1, int(chars_per_second * max_s))
    pieces: list[tuple[int, int]] = []
    for s, e in split_sentences(text):
        while spoken_chars(text[s:e]) > budget:
            cut = None
            for m in _CLAUSE.finditer(text, s, e):
                if spoken_chars(text[s : m.start()]) > budget:
                    break
                cut = m
            if cut is None or cut.start() <= s:  # no break point: hard cut by length
                k, n = s, 0
                while k < e and n < budget:
                    n += bool(text[k].isdigit() or script_of(text[k]))
                    k += 1
                pieces.append((s, k))
                s = k
            else:
                pieces.append((s, cut.start() + 1 if text[cut.start()] in ",;:،" else cut.start()))
                s = cut.end()
        if s < e:
            pieces.append((s, e))

    chunks: list[tuple[int, int]] = []
    for s, e in pieces:
        if chunks and spoken_chars(text[chunks[-1][0] : e]) <= budget:
            chunks[-1] = (chunks[-1][0], e)
        else:
            chunks.append((s, e))
    return chunks


def split_audio(
    x: np.ndarray, sr: int, max_s: float = 30.0, search_s: float = 5.0
) -> list[tuple[int, int]]:
    """Sample ranges of at most `max_s` seconds, cut at the quietest frame in the last
    `search_s` seconds before each limit, so cuts land in pauses rather than mid-word."""
    n_max = int(max_s * sr)
    if len(x) <= n_max:
        return [(0, len(x))]
    out = []
    start = 0
    while len(x) - start > n_max:
        lo = start + n_max - int(search_s * sr)
        db, _, hop = frame_features(x[lo : start + n_max], sr, 20)
        cut = lo + (int(np.argmin(db)) * hop + hop // 2 if len(db) else n_max - lo)
        out.append((start, cut))
        start = cut
    out.append((start, len(x)))
    return out


def to_wav(x: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, x, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def concat_wavs(wavs: list[bytes]) -> tuple[np.ndarray, int]:
    """Join synthesized chunks into one signal (for the waveform check)."""
    parts, rate = [], None
    for w in wavs:
        x, sr = load_audio(w)
        if rate is not None and sr != rate:
            raise ValueError(f"sample rates differ between chunks: {rate} vs {sr}")
        rate = sr
        parts.append(x)
    return (np.concatenate(parts) if parts else np.zeros(0, np.float32)), rate or 24000


# ---------------------------------------------------------------------------
# Model calls
# ---------------------------------------------------------------------------


async def synthesize(
    client: SpeechClient, text: str, lang: Language, voice: str, max_s: float = 25.0
) -> list[SpeechSegment]:
    """Indic-Speak, one request per ~25 s chunk. Each segment remembers its text span."""
    spans = chunk_text(text, lang.chars_per_second, max_s)
    results = await asyncio.gather(
        *(client.speak(text[s:e], lang.speech_code, voice) for s, e in spans)
    )
    return [SpeechSegment(r.wav, sp) for r, sp in zip(results, spans, strict=True)]


async def transcribe_long(client: SpeechClient, wav: bytes, language: str) -> str:
    """Indic-Transcribe on audio of any length (split at pauses into <= 30 s pieces)."""
    x, sr = load_audio(wav)
    ranges = split_audio(x, sr)
    if len(ranges) == 1:
        return (await client.transcribe(wav, language)).text
    results = await asyncio.gather(
        *(client.transcribe(to_wav(x[a:b], sr), language) for a, b in ranges)
    )
    return " ".join(r.text.strip() for r in results)


# ---------------------------------------------------------------------------
# Transcript comparison (pure; no model calls)
# ---------------------------------------------------------------------------


def _soft(tok: Token, main_script: str | None, text: str | None = None) -> bool:
    if any(ch.isdigit() for ch in tok.text):
        return True
    if text is not None and len(tok.text) <= 3:
        # A letter of a dotted, spelled-out acronym ("ए.आर.पी.ए" for ARPA): ASR may write it
        # in Latin or as one word, so a mismatch there is not a speech error.
        before = text[tok.start - 1] if tok.start > 0 else ""
        after = text[tok.end] if tok.end < len(text) else ""
        if before == "." or after == ".":
            nxt = text[tok.end + 1 : tok.end + 4] if after == "." else ""
            prv = text[max(0, tok.start - 4) : tok.start - 1] if before == "." else ""
            if (nxt and not nxt[:1].isspace()) or (prv and not prv[-1:].isspace()):
                return True
    scripts = {s for ch in tok.text if (s := script_of(ch))}
    return bool(main_script) and bool(scripts) and main_script not in scripts


def token_status(
    ref: list[Token], ops: list[Op], main_script: str | None, text: str | None = None
) -> list[str]:
    """Per reference token: 'ok' | 'sub' | 'del' | 'soft'.

    'soft' (numbers, other-script words) is never an error itself; it neither starts nor
    ends a missing stretch, but does not break one either ("…lost words 40 lost words…"
    is one stretch)."""
    st = ["ok"] * len(ref)
    for o in ops:
        if o.kind == "delete":
            for i in range(o.ref_start, o.ref_end):
                st[i] = "del"
        elif o.kind == "replace":
            common = min(o.ref_len, o.hyp_len)
            for k, i in enumerate(range(o.ref_start, o.ref_end)):
                st[i] = "sub" if k < common else "del"
    for i, t in enumerate(ref):
        if _soft(t, main_script, text):
            st[i] = "soft"
    return st


def _inserted_hyp(ops: list[Op]) -> set[int]:
    out: set[int] = set()
    for o in ops:
        if o.kind == "insert":
            out.update(range(o.hyp_start, o.hyp_end))
        elif o.kind == "replace" and o.hyp_len > o.ref_len:
            out.update(range(o.hyp_start + o.ref_len, o.hyp_end))
    return out


def _hyp_to_ref(ops: list[Op], n_hyp: int) -> list[int]:
    """For each hyp token, the ref token index it sits at (insertions map to the next ref)."""
    m = [0] * n_hyp
    for o in ops:
        for k, h in enumerate(range(o.hyp_start, o.hyp_end)):
            m[h] = (
                min(o.ref_start + k, max(o.ref_start, o.ref_end - 1)) if o.ref_len else o.ref_start
            )
    return m


def _contains_repeat(seq: list[str], gram: list[str], times: int) -> bool:
    n = len(gram)
    target = gram * times
    return any(seq[i : i + n * times] == target for i in range(len(seq) - n * times + 1))


def _time(span: tuple[int, int], text_len: int, duration: float | None):
    if not duration or not text_len:
        return None
    return (round(span[0] / text_len * duration, 2), round(span[1] / text_len * duration, 2))


def compare_transcript(
    text: str,
    asr: str,
    params: Params,
    *,
    offset: int = 0,
    full_len: int | None = None,
    duration: float | None = None,
    is_last: bool = True,
    audio_flags: list[Flag] | tuple[Flag, ...] = (),
) -> CheckResult:
    """Compare a transcript with the text it should match. Spans are shifted by `offset`
    (the text's position in the full spoken text); `duration` is the full audio length."""
    p = params.section("speech")
    min_missing = p.get("omission_min_tokens", 4)
    min_del_share = p.get("omission_min_del_share", 0.5)
    win = p.get("garbled_window", 6)
    win_err = p.get("garbled_min_error", 0.6)
    full_len = full_len if full_len is not None else len(text)

    ref, hyp = tokenize(text), tokenize(asr)
    res = CheckResult(NAME)
    if not ref:
        return CheckResult.skipped(NAME, "no words in spoken text")
    main = dominant_script(text)
    ops = align_tokens(ref, hyp)
    st = token_status(ref, ops, main, text)
    n_err = sum(s in ("sub", "del") for s in st)
    ins = len(_inserted_hyp(ops))
    res.signals = {
        "asr_text": asr,
        "ref_tokens": len(ref),
        "asr_tokens": len(hyp),
        "wer": round((n_err + ins) / len(ref), 4),
    }

    def shift(sp: tuple[int, int]) -> tuple[int, int]:
        return (sp[0] + offset, sp[1] + offset)

    def audio_overlap(sp: tuple[int, int], cats: tuple[str, ...]) -> bool:
        return any(f.span and f.category in cats and spans_overlap(f.span, sp) for f in audio_flags)

    duration_short = any(f.category == "speech_omission" and f.span is None for f in audio_flags)

    # --- omissions / truncation: runs of non-ok tokens dominated by deletions -------------
    covered = [False] * len(ref)
    i = 0
    while i < len(ref):
        if st[i] in ("ok", "soft"):
            i += 1
            continue
        j = i
        while j < len(ref) and st[j] != "ok":
            j += 1
        while st[j - 1] == "soft":  # a stretch never ends on a soft token
            j -= 1
        dels = sum(s == "del" for s in st[i:j])
        # Only soft tokens after it: a dropped ending with a number in it still reaches the end.
        reaches_end = all(s == "soft" for s in st[j:])
        if dels >= min_missing and dels / (j - i) >= min_del_share:
            span = shift((ref[i].start, ref[j - 1].end))
            truncated = is_last and reaches_end
            share = (j - i) / len(ref)
            conf = min(0.85, 0.55 + 0.05 * (dels - min_missing))
            if duration_short:
                conf = min(0.9, conf + 0.1)
            if truncated:
                conf = max(conf, 0.7)
            missing = " ".join(t.text for t in ref[i:j])
            res.flags.append(
                Flag(
                    "speech_omission",
                    "critical" if truncated and share >= 0.3 else "major",
                    NAME,
                    round(conf, 3),
                    span,
                    (
                        f"audio stops early: the last {j - i} words are not in the transcript "
                        if truncated
                        else f"{j - i} words are missing from the transcript "
                    )
                    + f"(“{missing[:80]}”)",
                    time_span=_time(span, full_len, duration),
                    details={
                        "kind": "truncation" if truncated else "omission",
                        "deleted_tokens": dels,
                        "run_tokens": j - i,
                    },
                )
            )
            for k in range(i, j):
                covered[k] = True
        i = max(j, i + 1)

    # --- repetition: repeated n-grams in ASR, made of inserted tokens, absent from text --
    hyp_norm = [t.norm for t in hyp]
    ref_norm = [t.norm for t in ref]
    inserted = _inserted_hyp(ops)
    h2r = _hyp_to_ref(ops, len(hyp))
    for start, n, times in repeated_ngrams(hyp_norm, min_n=1):
        extra = n * (times - 1)
        if n == 1 and times < 3:
            continue  # "very very" style doubling is too often genuine or ASR noise
        if _contains_repeat(ref_norm, hyp_norm[start : start + n], times):
            continue  # the text itself repeats it
        rng = range(start, start + n * times)
        if sum(h in inserted for h in rng) < extra / 2:
            continue
        r0 = h2r[start]
        r1 = min(len(ref) - 1, max(r0, h2r[start + n - 1]))
        span = shift((ref[r0].start, ref[r1].end))
        gram = " ".join(t.text for t in hyp[start : start + n])
        res.flags.append(
            Flag(
                "speech_repetition",
                "major" if extra > 2 else "minor",
                NAME,
                0.7,
                span,
                f"transcript repeats “{gram}” {times}× where the text says it once",
                time_span=_time(span, full_len, duration),
                details={"ngram": gram, "times": times, "extra_tokens": extra},
            )
        )

    # --- garbled: windows of mostly wrong tokens (not already explained as omission) ------
    bad = [s in ("sub", "del") and not covered[k] for k, s in enumerate(st)]
    windows: list[tuple[int, int]] = []
    if len(ref) >= win:
        for k in range(len(ref) - win + 1):
            if sum(bad[k : k + win]) / win >= win_err:
                if windows and k <= windows[-1][1]:
                    windows[-1] = (windows[-1][0], k + win)
                else:
                    windows.append((k, k + win))
    for a, b in windows:
        while a < b and not bad[a]:
            a += 1
        while b > a and not bad[b - 1]:
            b -= 1
        span = shift((ref[a].start, ref[b - 1].end))
        backed = audio_overlap(span, ("speech_garbled",))
        err = sum(bad[a:b]) / (b - a)
        res.flags.append(
            Flag(
                "speech_garbled",
                "major",
                NAME,
                0.7 if backed else 0.3,
                span,
                f"{err:.0%} of {b - a} words here do not match the transcript"
                + ("; the waveform check also found noise/silence here" if backed else ""),
                time_span=_time(span, full_len, duration),
                details={"error_rate": round(err, 2), "audio_backed": backed},
            )
        )
    return res


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def check_speech(
    client: SpeechClient,
    text: str,
    lang: Language,
    params: Params,
    segments: list[SpeechSegment],
    *,
    audio_flags: list[Flag] | tuple[Flag, ...] = (),
    duration: float | None = None,
) -> CheckResult:
    """Transcribe every segment and compare it with its part of `text`.

    `segments` come from `synthesize` (one per chunk, with spans) or are a single supplied
    recording with `text_span=None`. `audio_flags` are the waveform check's flags on the
    same audio, used to back up garbled-speech evidence.
    """
    if not text.strip():
        return CheckResult.skipped(NAME, "empty text")
    if not segments:
        return CheckResult.skipped(NAME, "no audio")
    asr = await asyncio.gather(
        *(transcribe_long(client, s.wav, lang.speech_code) for s in segments)
    )

    out = CheckResult(NAME, signals={"segments": []})
    ref_total = err_total = 0.0
    for k, (seg, hyp) in enumerate(zip(segments, asr, strict=True)):
        s, e = seg.text_span or (0, len(text))
        r = compare_transcript(
            text[s:e],
            hyp,
            params,
            offset=s,
            full_len=len(text),
            duration=duration,
            is_last=k == len(segments) - 1,
            audio_flags=audio_flags,
        )
        if not r.ran:
            continue
        out.flags.extend(r.flags)
        out.signals["segments"].append({"span": [s, e], **r.signals})
        ref_total += r.signals["ref_tokens"]
        err_total += r.signals["wer"] * r.signals["ref_tokens"]
    out.signals["wer"] = round(err_total / ref_total, 4) if ref_total else None
    out.signals["asr_text"] = " ".join(asr)
    return out
