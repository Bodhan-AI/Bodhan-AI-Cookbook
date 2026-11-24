"""Speech checks: audio-signal sanity and ASR back-transcription alignment.

Separating TTS errors from ASR (checker) errors relies on structure, not on raw error rate:
* ASR mistakes are mostly *scattered, short substitutions* (near-miss spellings, split compounds,
  numbers written as words). The fuzzy, wildcard-aware alignment absorbs most of them.
* TTS omissions are *contiguous deletion runs* (at the end for truncation) and come with a
  shorter-than-expected duration. TTS repetitions are *insertion runs that copy neighbouring
  words* and come with a longer duration. Garbled audio shows up in the signal (silence, noise,
  clipping) and as a stretch of very low-similarity alignment.
A deletion run is trusted more when the duration agrees, and less when it does not.
"""
from __future__ import annotations

import numpy as np
from rapidfuzz import fuzz

from ..text.align import align, error_rate, runs
from ..text.markup import speakable
from ..text.normalize import WORD_RE, normalize
from .base import Check, Context, Signal, register

FRAME_S = 0.02


def spoken_tokens(translation: str) -> list[tuple[str, int, int]]:
    """Tokens of the speakable text (speech-normalized) with offsets into the translation."""
    plain, pidx = speakable(translation)
    n = normalize(plain, "speech")
    toks = []
    for m in WORD_RE.finditer(n.text):
        s, e = n.to_original(m.start(), m.end())
        toks.append((m.group(0), pidx[s], pidx[e - 1] + 1))
    return toks


def asr_tokens(text: str) -> list[str]:
    return WORD_RE.findall(normalize(text, "speech").text)


def find_loops(hyp: list[str], min_len: int = 3) -> list[tuple[int, int]]:
    """(start, length) of phrases in the transcript that are immediately repeated (fuzzy)."""
    found, i, n = [], 0, len(hyp)
    while i < n:
        hit = None
        for length in range((n - i) // 2, min_len - 1, -1):
            a, b = " ".join(hyp[i:i + length]), " ".join(hyp[i + length:i + 2 * length])
            if fuzz.ratio(a, b) >= 88:
                hit = length
                break
        if hit:
            found.append((i, hit))
            i += 2 * hit
        else:
            i += 1
    return found


def best_window(phrase: str, ref: list[str], length: int) -> tuple[int, int]:
    """Reference token window [j0, j1) most similar to `phrase`."""
    best, arg = -1.0, (0, min(length, len(ref)))
    for size in {max(1, length - 2), length, length + 2}:
        for j in range(0, max(1, len(ref) - size + 1)):
            s = fuzz.ratio(phrase, " ".join(ref[j:j + size]))
            if s > best:
                best, arg = s, (j, min(j + size, len(ref)))
    return arg


def frame_db(x: np.ndarray, sr: int) -> np.ndarray:
    hop = max(1, int(FRAME_S * sr))
    n = len(x) // hop
    if n == 0:
        return np.zeros(0)
    fr = x[: n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(fr ** 2, axis=1) + 1e-12)
    return 20 * np.log10(rms)


def spectral_flatness(x: np.ndarray, sr: int) -> np.ndarray:
    hop = max(1, int(FRAME_S * sr))
    n = len(x) // hop
    if n == 0:
        return np.zeros(0)
    fr = x[: n * hop].reshape(n, hop) * np.hanning(hop)
    mag = np.abs(np.fft.rfft(fr, axis=1)) + 1e-10
    return np.exp(np.mean(np.log(mag), axis=1)) / np.mean(mag, axis=1)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    out, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, len(mask)))
    return out


def time_to_span(t0: float, t1: float, dur: float, toks: list[tuple[str, int, int]]) -> tuple[int, int] | None:
    """Approximate: map a time range to tokens proportionally (speech rate assumed uniform)."""
    if not toks or dur <= 0:
        return None
    n = len(toks)
    a = min(n - 1, max(0, int(t0 / dur * n)))
    b = min(n - 1, max(a, int(np.ceil(t1 / dur * n)) - 1))
    return toks[a][1], toks[b][2]


def speech_rate(ctx: Context) -> tuple[float, float] | None:
    st = ctx.stats.get("speech_rate", {})
    b = st.get(ctx.item.lang) if st.get(ctx.item.lang, (0, 0, 0))[2] >= 5 else st.get("_all")
    return (b[0], b[1]) if b else None


@register
class AudioSignalCheck(Check):
    name = "audio_signal"
    area = "speech"
    tier = 0

    def applies(self, ctx: Context) -> bool:
        return ctx.artifacts.get("audio") is not None

    async def run(self, ctx: Context) -> list[Signal]:
        x, sr = ctx.artifacts["audio"]
        dur = len(x) / sr
        toks = spoken_tokens(ctx.translation)
        out: list[Signal] = []
        ev = {"duration_s": round(dur, 2)}
        if dur < 0.3 or np.max(np.abs(x)) < 1e-3:
            return [Signal(self.name, "speech_garbled", "critical", 0.95, "Audio is empty or silent.", (0, len(ctx.translation)), None, ev)]
        db = frame_db(x, sr)
        voiced_ref = np.percentile(db, 95)
        silent = db < max(-55.0, voiced_ref - 35.0)
        speech_frames = np.where(~silent)[0]
        if len(speech_frames):
            first, last = speech_frames[0], speech_frames[-1]
            for a, b in _runs(silent[first:last + 1]):
                length = (b - a) * FRAME_S
                if length >= 1.2:
                    t0, t1 = (first + a) * FRAME_S, (first + b) * FRAME_S
                    out.append(Signal(self.name, "speech_garbled", "major", min(0.85, 0.55 + 0.1 * (length - 1.2)),
                                      f"Long silence of {length:.1f}s in the middle of speech.", time_to_span(t0, t1, dur, toks), None,
                                      {**ev, "silence_s": round(length, 2), "at_s": [round(t0, 2), round(t1, 2)], "span_is_approximate": True}))
        # clipping
        clipped = np.abs(x) >= 0.999
        if clipped.mean() > 0.002:
            out.append(Signal(self.name, "speech_garbled", "minor", 0.6, f"{clipped.mean():.1%} of samples are clipped.", None, None,
                              {**ev, "clipped_fraction": round(float(clipped.mean()), 4)}))
        # broadband noise: loud frames with a flat (noise-like) spectrum
        flat = spectral_flatness(x, sr)
        k = min(len(flat), len(db))
        noisy = (flat[:k] > 0.45) & (db[:k] > voiced_ref - 20)
        for a, b in _runs(noisy):
            length = (b - a) * FRAME_S
            if length >= 0.4:
                t0, t1 = a * FRAME_S, b * FRAME_S
                out.append(Signal(self.name, "speech_garbled", "major", min(0.85, 0.6 + 0.2 * length),
                                  f"{length:.1f}s of noise-like audio.", time_to_span(t0, t1, dur, toks), None,
                                  {**ev, "noise_s": round(length, 2), "at_s": [round(t0, 2), round(t1, 2)], "span_is_approximate": True}))
        # duration vs the dataset's own speech rate (chars of spoken text per second)
        rate = speech_rate(ctx)
        chars = sum(len(t[0]) for t in toks)
        if rate and chars >= 15 and len(speech_frames):
            spoken_s = (speech_frames[-1] - speech_frames[0] + 1) * FRAME_S
            z = (np.log(chars / max(spoken_s, 0.1)) - rate[0]) / rate[1]
            ctx.artifacts["duration_z"] = float(z)  # >0: too fast/short (omission); <0: too slow/long (repetition)
            ev["duration_z"] = round(float(z), 2)
        ctx.artifacts["audio_stats"] = ev
        return out


@register
class AsrAlignmentCheck(Check):
    name = "asr_alignment"
    area = "speech"
    tier = 1

    def applies(self, ctx: Context) -> bool:
        return ctx.artifacts.get("asr_text") is not None

    async def run(self, ctx: Context) -> list[Signal]:
        toks = spoken_tokens(ctx.translation)
        hyp = asr_tokens(ctx.artifacts["asr_text"])
        if not toks:
            return []
        ref = [t[0] for t in toks]
        wild = [any(c.isdigit() for c in t) or t.isascii() for t in ref]
        ops = align(ref, hyp, wild)
        er = error_rate(ops, len(ref))
        ctx.artifacts["asr_error_rate"] = round(er, 4)
        dz = ctx.artifacts.get("duration_z")
        out: list[Signal] = []
        n = len(ref)

        def span_of(i0, i1):
            i1 = max(i1, i0 + 1)
            return toks[i0][1], toks[min(i1, n) - 1][2]

        # omission / truncation: contiguous deletion runs
        for r in runs(ops, "del"):
            at_end = r.ref[1] >= n and r.hyp[0] >= len(hyp) - 1
            need = max(2, round(0.10 * n)) if at_end else max(3, round(0.12 * n))
            if r.length < need:
                continue
            conf = 0.6 + min(0.25, 0.02 * r.length)
            if dz is not None:
                conf += 0.15 if dz > 1.0 else (-0.2 if dz < -0.5 else 0.0)
            sev = "critical" if r.length >= 0.3 * n else "major"
            kind = "The end of the text was not spoken" if at_end else "Words were not spoken"
            out.append(Signal(self.name, "speech_omission", sev, max(0.2, min(conf, 0.95)), f"{kind} ({r.length} of {n} words missing from the transcript).",
                              span_of(*r.ref), None, {"missing_words": r.length, "total_words": n, "truncation": at_end, "asr_error_rate": round(er, 3),
                                                      "duration_z": None if dz is None else round(dz, 2)}))
        # repetition: inserted hypothesis words that copy the words just before them
        for r in runs(ops, "ins"):
            if r.length < 2:
                continue
            ins = hyp[r.hyp[0]:r.hyp[1]]
            before = hyp[max(0, r.hyp[0] - r.length):r.hyp[0]]
            after = hyp[r.hyp[1]:r.hyp[1] + r.length]
            sim = max(fuzz.ratio(" ".join(ins), " ".join(before)), fuzz.ratio(" ".join(ins), " ".join(after)))
            if sim < 70:
                continue
            j0, j1 = best_window(" ".join(ins), ref, r.length)  # locate the repeated words in the text
            conf = 0.7 + min(0.2, 0.03 * r.length) + (0.1 if dz is not None and dz < -1.0 else 0.0)
            out.append(Signal(self.name, "speech_repetition", "major", min(conf, 0.95), f"A phrase of {r.length} words is spoken twice.",
                              span_of(j0, j1), None, {"repeated_words": r.length, "similarity": round(sim, 1)}))
        # repetition, found directly in the transcript: a phrase immediately followed by (nearly) itself,
        # which the reference does not contain twice. Independent of the alignment, which number wildcards
        # can fragment when the repeated phrase contains digits spoken as words.
        for i, length in find_loops(hyp):
            phrase = " ".join(hyp[i:i + length])
            if fuzz.partial_ratio(phrase + " " + phrase, " ".join(ref)) >= 88:
                continue  # the text itself repeats this phrase
            j0, j1 = best_window(phrase, ref, length)
            span = span_of(j0, j1)
            if any(s.category == "speech_repetition" and s.span and s.span[0] < span[1] and span[0] < s.span[1] for s in out):
                continue
            conf = 0.8 + (0.1 if dz is not None and dz < -1.0 else 0.0)
            out.append(Signal(self.name, "speech_repetition", "major", conf, f"A phrase of {length} words is spoken twice in a row.",
                              span, None, {"repeated_words": length, "method": "transcript loop"}))
        # garbled: a stretch where ASR finds almost nothing similar
        bad = [o for o in ops if o.kind == "sub" and o.cost > 0.6]
        if bad:
            cluster: list = []
            for o in ops:
                if o.kind == "sub" and o.cost > 0.6 or (cluster and o.kind in ("del", "ins")):
                    cluster.append(o)
                    continue
                self._garbled(cluster, n, span_of, out)
                cluster = []
            self._garbled(cluster, n, span_of, out)
        ctx.artifacts["asr_ops"] = [(o.kind, o.ref, o.hyp) for o in ops if o.kind != "match"][:60]
        return out

    @staticmethod
    def _garbled(cluster, n, span_of, out):
        subs = [o for o in cluster if o.kind == "sub"]
        if len(subs) >= max(3, round(0.1 * n)):
            out.append(Signal("asr_alignment", "speech_garbled", "major", 0.55, f"{len(subs)} consecutive words are unintelligible in the transcript.",
                              span_of(cluster[0].ref[0], cluster[-1].ref[1]), None, {"unintelligible_words": len(subs)}))
