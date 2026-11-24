"""Speech checks.

``audio_stats`` (tier 0, local): duration, silences, clipping, broadband noise,
speaking rate vs this language's median rate in the dataset.

``asr`` (tier 1): back-transcribes the audio with Indic-Transcribe in <= 30 s
chunks cut at pauses, aligns the transcript word-by-word with the text that was
spoken, and turns alignment gaps into candidates:

* text words with no transcript counterpart -> omission (truncation if at the end)
* transcript words with no text counterpart that repeat neighbouring text -> repetition
* transcript n-grams that occur more often than in the text -> repetition
* a transcript that matches the text badly everywhere -> garbled

Separating ASR mistakes from real speech errors:

* a real omission is a *contiguous* run of missing words; ASR noise is scattered
  substitutions, so only runs of >= ``asr_min_deleted_words`` count;
* duration must agree: a real omission makes the audio shorter than this
  language's speaking rate predicts, a real repetition makes it longer;
* every omission/repetition candidate gets a second-opinion transcription with
  different chunk boundaries. If the missing words appear in the second
  transcript (or the repetition does not), the candidate is dismissed as checker
  noise and logged in ``dismissed`` with both transcripts' evidence.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .. import audio as A
from .. import markup
from ..config import defaults, lang_info, langs
from ..models import ItemResult
from ..normalize import normalize_text, word_tokens
from ..spans import char_error_rate, similarity, word_alignment
from .base import Check, SkipCheck


def speakable(text: str) -> str:
    """Mask markup with spaces so offsets stay identical to the original text."""
    chars = list(text)

    def blank(a: int, b: int) -> None:
        for k in range(a, b):
            if chars[k] != "\n":
                chars[k] = " "

    for s in markup.math_segments(text):
        blank(s.start, s.end)
    for m in markup.LINK_RE.finditer(text):
        blank(m.start(2) - 2, m.end())  # keep link text, drop "](url)"
        blank(m.start(), m.start() + 1)
    for m in markup.URL_RE.finditer(text):
        blank(m.start(), m.end())
    for line in markup.md_lines(text):
        if line.kind == "table_sep":  # |---|:---:| is layout, not words
            blank(line.start, line.end)
    for m in re.finditer(r"^\s*(?:#{1,6}|[-*+>]|\d+[.)])\s", text, re.M):
        blank(m.start(), m.end())
    for m in re.finditer(r"[|*_`#~]|^\s*:?-{3,}:?\s*$", text, re.M):
        blank(m.start(), m.end())
    return "".join(chars)


def speakable_for_tts(text: str) -> str:
    return re.sub(r"\s+", " ", speakable(text)).strip()


# ---------------------------------------------------------------------------
# time -> position in the text (K5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Anchor:
    """Audio from t0 to t1 (seconds) speaks the text's characters c0..c1."""

    t0: float
    t1: float
    c0: int
    c1: int


@dataclass
class TimeMap:
    """Seconds -> character offsets in the text that was spoken.

    Inside an anchor, position follows *speaking* time: only frames with sound count, so a pause (an inserted
    silence, a slow start) does not drag later positions forward. Without a transcript there is one anchor,
    the whole clip. With one, every ASR chunk is an anchor: the alignment found its words in the text, so its
    time range maps to exactly those characters, whatever the speaking rate in the rest of the clip.
    """

    text: str
    anchors: list[Anchor]
    active: np.ndarray  # per 20 ms frame: sound or not
    method: str  # "proportional" | "asr_anchors"

    def char_at(self, t: float) -> int:
        if not self.anchors:
            return 0
        if t <= self.anchors[0].t0:
            return self.anchors[0].c0
        if t >= self.anchors[-1].t1:
            return self.anchors[-1].c1
        a = next(a for a in self.anchors if t < a.t1)
        f0, f1, ft = (int(v / A.FRAME_S) for v in (a.t0, a.t1, t))
        total = int(self.active[f0:f1].sum())
        frac = int(self.active[f0:ft].sum()) / total if total else (ft - f0) / max(1, f1 - f0)
        return a.c0 + round(frac * (a.c1 - a.c0))

    def span(self, t0: float, t1: float) -> tuple[int, int]:
        """Character span for a stretch of audio, widened to whole words (at least one)."""
        a, b = self.char_at(t0), self.char_at(t1)
        return _snap(self.text, min(a, b), max(a, b))


def _word_bounds(text: str) -> tuple[int, int]:
    toks = word_tokens(speakable(text))
    return (toks[0].start, toks[-1].end) if toks else (0, len(text))


def proportional_map(x: np.ndarray, sr: int, text: str) -> TimeMap:
    """One anchor for the whole clip: used by the waveform check, which runs before any transcript exists."""
    c0, c1 = _word_bounds(text)
    return TimeMap(text, [Anchor(0.0, len(x) / sr, c0, c1)], A.active_mask(x, sr), "proportional")


def anchored_map(
    x: np.ndarray,
    sr: int,
    text: str,
    ref_toks: list,
    ref: list[str],
    hyp: list[str],
    hyp_chunk: list[int],
    plan: list[tuple[int, int]],
) -> TimeMap:
    """One anchor per ASR chunk: a chunk spans the text from its first aligned word to the next chunk's.

    Text words the transcript missed join the chunk before them; assignments only move forward, so the
    anchors stay in order. Falls back to the proportional map when nothing aligned.
    """
    chunk_of: list[int | None] = [None] * len(ref)
    for tag, i1, i2, j1, j2 in word_alignment(ref, hyp):
        if tag in ("equal", "replace"):
            for k, j in zip(range(i1, i2), range(j1, j2)):
                chunk_of[k] = hyp_chunk[j]
    known = [c for c in chunk_of if c is not None]
    if not known or not ref_toks:
        return proportional_map(x, sr, text)
    filled, last = [], known[0]
    for c in chunk_of:
        last = max(last, c) if c is not None else last
        filled.append(last)
    firsts: list[int | None] = [
        next((ref_toks[k].start for k, c in enumerate(filled) if c == i), None) for i in range(len(plan))
    ]
    firsts[0] = ref_toks[0].start
    end: int = ref_toks[-1].end
    anchors: list[Anchor] = []
    for i, (a, b) in enumerate(plan):
        own = firsts[i]
        c0: int = own if own is not None else anchors[-1].c1  # a chunk with no words: zero width
        later = [f for f in firsts[i + 1 :] if f is not None]
        c1: int = later[0] if later else end
        anchors.append(Anchor(a / sr, b / sr, c0, max(c0, c1)))
    return TimeMap(text, anchors, A.active_mask(x, sr), "asr_anchors")


def relocate(r: ItemResult, tm: TimeMap) -> None:
    """Re-place the waveform check's time-stamped findings once the transcript gives anchors."""
    for s in r.signals:
        t = s.details.get("time_s")
        if s.check == "audio_stats" and t:
            s.span = tm.span(t[0], t[1])
            s.details["located_by"] = tm.method


def _snap(text: str, a: int, b: int) -> tuple[int, int]:
    """Widen a character span to whole words."""
    while a > 0 and not text[a - 1].isspace():
        a -= 1
    while b < len(text) and not text[b].isspace():
        b += 1
    return a, max(a, b)


def _rate(ctx, lang: str) -> tuple[float, int]:
    return ctx.stats.chars_per_second.get(lang, (langs()["priors"]["chars_per_second"], 0))


NUMERAL_RE = re.compile(r"[\d.,:/-]*\d[\d.,:/-]*")  # a number written in digits: 365, 2,500, ३६५, 3.14, 1/2
EDGE_PUNCT = "\"'()[]{}.,;:!?।॥"


def spoken_chars(text: str) -> float:
    """Characters of speech the text takes to say. A number is read out as words ('365' -> 'three hundred
    and sixty five'), so each of its digits counts as ``speech_chars_per_digit`` characters, not one."""
    per_digit = float(defaults()["check_params"]["speech_chars_per_digit"])
    total = 0.0
    for tok in speakable(text).split():
        core = tok.strip(EDGE_PUNCT)
        if core and NUMERAL_RE.fullmatch(core):
            total += per_digit * sum(ch.isdigit() for ch in core) + len(tok) - len(core)
        else:
            total += len(tok)
    return total


def numbers_read_out(ref: list[str], hyp: list[str]) -> tuple[set[int], set[int], int]:
    """Text numerals the speech read out as words: (text word indices, transcript word indices, count).

    The transcript then has words where the text has digits; in Tamil, Indic-Speak even read '365' in English
    (live, 23 Nov). An alignment block whose text side is only numerals is explained by that: it is neither
    missing nor garbled speech. Language-independent: no number-to-words tables. (Whether the spoken number
    is the *right* number is not checked here.)
    """
    blocks: list[list[int]] = []
    cur: list[int] | None = None
    for tag, i1, i2, j1, j2 in word_alignment(ref, hyp):
        if tag == "equal":
            if cur:
                blocks.append(cur)
            cur = None
        else:
            cur = [i1, i2, j1, j2] if cur is None else [cur[0], i2, cur[2], j2]
    if cur:
        blocks.append(cur)
    text_words: set[int] = set()
    spoken_words: set[int] = set()
    n = 0
    for i1, i2, j1, j2 in blocks:
        if i2 > i1 and all(NUMERAL_RE.fullmatch(ref[k]) for k in range(i1, i2)):
            text_words.update(range(i1, i2))
            spoken_words.update(range(j1, j2))
            n += 1
    return text_words, spoken_words, n


class AudioStatsCheck(Check):
    name = "audio_stats"
    area = "speech"
    tier = 0
    description = "Duration vs speaking rate, long or edge silences, clipping and broadband noise from the waveform."

    def applies(self, r, ctx):
        return bool(r.item.audio)

    def run(self, r: ItemResult, ctx) -> None:
        x, sr = ctx.load_audio(r.item.audio)
        st = A.analyse(x, sr)
        r.state["audio_stats"] = st
        r.metrics["audio"] = st.summary()
        text = r.item.translation or ""
        n = len(text)
        p = ctx.params
        if st.duration_s < 0.2 or st.active_s < 0.1:
            self.signal(
                r,
                "speech_garbled",
                0.95,
                f"Audio is empty or silent ({st.duration_s:.2f} s, {st.active_s:.2f} s of sound)",
                severity="critical",
                span=(0, n) if n else None,
            )
            return
        # Findings carry their time; the span is a first guess that the asr check re-places with anchors (K5).
        tm = proportional_map(x, sr, text) if n else None

        def where(a: float, b: float) -> dict[str, Any]:
            return {"time_s": [round(a, 2), round(b, 2)], "located_by": "proportional"}

        ls = st.longest_silence
        if ls and ls[1] - ls[0] >= p["audio_long_silence_s"]:
            gap = ls[1] - ls[0]
            self.signal(
                r,
                "speech_garbled",
                min(0.85, 0.55 + 0.1 * (gap - p["audio_long_silence_s"])),
                f"Silence of {gap:.1f} s inside the speech at {ls[0]:.1f}-{ls[1]:.1f} s",
                severity="major",
                span=tm.span(*ls) if tm else None,
                details=where(*ls),
            )
        for edge, length in (("leading", st.lead_silence_s), ("trailing", st.trail_silence_s)):
            if length >= p["audio_edge_silence_s"]:
                span = (0, 0) if edge == "leading" else (n, n)
                self.signal(
                    r,
                    "speech_garbled",
                    0.5,
                    f"{length:.1f} s of {edge} silence",
                    severity="minor",
                    span=span if n else None,
                )
        if st.clip_ratio >= p["audio_clip_ratio"]:
            a, b = st.clip_regions[0] if st.clip_regions else (0.0, st.duration_s)
            self.signal(
                r,
                "speech_garbled",
                min(0.9, 0.6 + 20 * st.clip_ratio),
                f"Clipping: {st.clip_ratio:.2%} of samples at full scale (first at {a:.1f} s)",
                severity="major",
                span=tm.span(a, b) if tm else None,
                details={**where(a, b), "clip_regions_s": [[round(u, 2), round(v, 2)] for u, v in st.clip_regions[:5]]},
            )
        for a, b in st.noise_runs[:3]:
            self.signal(
                r,
                "speech_garbled",
                min(0.85, 0.6 + 0.2 * (b - a)),
                f"{b - a:.1f} s of broadband noise at {a:.1f}-{b:.1f} s",
                severity="major",
                span=tm.span(a, b) if tm else None,
                details=where(a, b),
            )
        if text:
            rate, k = _rate(ctx, r.item.lang)
            expected = spoken_chars(text) / rate
            ratio = st.active_s / max(expected, 1e-6)
            r.metrics["duration_ratio"] = round(ratio, 3)
            r.state["duration_ratio"] = ratio
            if ratio < p["duration_short_ratio"]:
                self.signal(
                    r,
                    "speech_omission",
                    0.3,
                    f"Speech lasts {st.active_s:.1f} s, {ratio:.0%} of the {expected:.1f} s expected for this text at {rate:.1f} chars/s (n={k})",
                    span=None,
                )
            elif ratio > p["duration_long_ratio"]:
                self.signal(
                    r,
                    "speech_repetition",
                    0.25,
                    f"Speech lasts {st.active_s:.1f} s, {ratio:.0%} of the {expected:.1f} s expected for this text (n={k})",
                    span=None,
                )


class AsrCheck(Check):
    name = "asr"
    area = "speech"
    tier = 1
    description = "Back-transcription with Indic-Transcribe (<=30 s chunks), word alignment, second-opinion re-transcription for candidates."

    def applies(self, r, ctx):
        return bool(r.item.audio and r.item.translation)

    def _transcribe(self, ctx, x, sr, plan, lang) -> tuple[str, list[str], list[int]]:
        """(raw transcript, normalised words, chunk index of each word): one request per chunk."""
        texts, words, chunk_of = [], [], []
        for i, (a, b) in enumerate(plan):
            t = ctx.bodhan.transcribe(A.wav_bytes(x[a:b], sr), lang)
            texts.append(t)
            ws = [normalize_text(w.text, asr_fold=True) for w in word_tokens(t)]
            words += ws
            chunk_of += [i] * len(ws)
        return " ".join(t for t in texts if t), words, chunk_of

    def run(self, r: ItemResult, ctx) -> None:
        info = lang_info(r.item.lang)
        text = self.translation(r)
        p = ctx.params
        x, sr = ctx.load_audio(r.item.audio)
        spk = speakable(text)
        ref_toks = word_tokens(spk)
        if not ref_toks:
            raise SkipCheck("no speakable words in the text")
        plan = A.chunk_plan(x, sr, p["asr_chunk_target_s"], p["asr_chunk_max_s"])
        hyp, hyp_words, hyp_chunk = self._transcribe(ctx, x, sr, plan, info.asr_lang)
        r.state["transcript"] = hyp
        ref = [normalize_text(t.text, asr_fold=True) for t in ref_toks]
        cer = char_error_rate(" ".join(ref), " ".join(hyp_words))
        r.metrics["asr_cer"] = round(cer, 4)
        r.metrics["asr_words"] = len(hyp_words)
        r.metrics["asr_chunks"] = len(plan)
        relocate(r, anchored_map(x, sr, text, ref_toks, ref, hyp_words, hyp_chunk, plan))
        num_ref, num_hyp, n_numbers = numbers_read_out(ref, hyp_words)
        r.metrics["asr_numbers_read_out"] = n_numbers
        cands = self._candidates(r, ctx, text, ref_toks, ref, hyp_words, damp=1.0, skip=num_ref)
        # Garbled = poor match *outside* the contiguous gaps already explained as omission/repetition
        # and outside numbers read out as words,
        # so a clean truncation is not double-reported as unintelligible speech.
        gap_words = {k for c in cands if c["category"] == "speech_omission" for k in range(*c["words"])} | num_ref
        residual_ref = [w for k, w in enumerate(ref) if k not in gap_words]
        residual_hyp = [w for j, w in enumerate(hyp_words) if j not in num_hyp]
        residual_cer = char_error_rate(" ".join(residual_ref), " ".join(residual_hyp)) if residual_ref else 0.0
        r.metrics["asr_residual_cer"] = round(residual_cer, 4)
        garbled = residual_cer >= p["asr_garbled_cer"]
        if garbled:  # an unintelligible transcript repeats junk words by chance: no doubled-phrase evidence
            cands = [c for c in cands if c.get("pair") is None]
            self.signal(
                r,
                "speech_garbled",
                min(0.85, 0.45 + 0.5 * (residual_cer - p["asr_garbled_cer"])),
                f"Transcript matches the text poorly even outside missing/repeated stretches "
                f"(character error rate {residual_cer:.0%}): speech may be unintelligible",
                severity="major",
                span=(0, len(text)),
                details={"cer": round(cer, 3), "residual_cer": round(residual_cer, 3)},
            )
            for c in cands:
                c["score"] *= 0.5  # alignment gaps are unreliable when the whole transcript is poor
        if not cands:
            return
        alt_plan = A.alt_chunk_plan(x, sr, plan, p["asr_chunk_max_s"])
        _, alt_words, _ = self._transcribe(ctx, x, sr, alt_plan, info.asr_lang)
        alt_ok = self._matched_mask(ref, alt_words)
        alt_counts = Counter(zip(alt_words, alt_words[1:], alt_words[2:]))
        ratio = r.state.get("duration_ratio")
        for c in cands:
            i1, i2 = c["words"]
            if c["category"] == "speech_omission":
                recovered = sum(alt_ok[i1:i2]) / max(1, i2 - i1)
                if recovered >= p["asr_second_opinion_recovery"]:
                    r.dismissed.append(
                        {
                            "check": self.name,
                            "category": c["category"],
                            "span": list(c["span"]),
                            "reason": f"second transcription (different chunking) contains {recovered:.0%} of the 'missing' words: likely an ASR miss",
                        }
                    )
                    continue
                c["score"] += 0.15
                c["evidence"] += f"; confirmed by a second transcription ({recovered:.0%} recovered)"
                if ratio is not None and ratio < 0.92:
                    c["score"] += 0.1
                    c["evidence"] += f"; audio is shorter than expected ({ratio:.0%})"
                elif ratio is not None and ratio > 1.05 and i2 < len(ref):
                    c["score"] -= 0.15
                    c["evidence"] += f"; but audio length is normal ({ratio:.0%})"
            else:
                g = c.get("ngram")
                pair = c.get("pair")
                if (g is not None and alt_counts[g] < 2) or (pair is not None and not _doubled(alt_words, pair)):
                    r.dismissed.append(
                        {
                            "check": self.name,
                            "category": c["category"],
                            "span": list(c["span"]),
                            "reason": "repeated phrase not present in a second transcription: likely an ASR artefact",
                        }
                    )
                    continue
                c["score"] += 0.1
                if ratio is not None and ratio > 1.08:
                    c["score"] += 0.1
                    c["evidence"] += f"; audio is longer than expected ({ratio:.0%})"
            self.signal(
                r,
                c["category"],
                c["score"],
                c["evidence"],
                severity=c["severity"],
                span=c["span"],
                details=c.get("details", {}),
            )

    @staticmethod
    def _matched_mask(ref: list[str], hyp: list[str]) -> list[bool]:
        ok = [False] * len(ref)
        for tag, i1, i2, j1, j2 in word_alignment(ref, hyp):
            if tag == "equal":
                for k in range(i1, i2):
                    ok[k] = True
            elif tag == "replace":
                for k, m in zip(range(i1, i2), range(j1, j2)):
                    ok[k] = similarity(ref[k], hyp[m]) >= 0.6
        return ok

    def _candidates(self, r, ctx, text, ref_toks, ref, hyp, damp: float, skip: set[int] | None = None) -> list[dict]:
        """Omission/repetition candidates from alignment gaps; gaps that are only ``skip`` words (numbers read
        out as words) are not candidates."""
        p = ctx.params
        ops = word_alignment(ref, hyp)
        # merge consecutive non-equal blocks (allowing a 1-word 'equal' island inside a gap)
        gaps, cur = [], None
        for k, (tag, i1, i2, j1, j2) in enumerate(ops):
            if tag != "equal" or (cur and i2 - i1 == 1 and k + 1 < len(ops) and ops[k + 1][0] != "equal"):
                cur = [i1, i2, j1, j2] if cur is None else [cur[0], i2, cur[2], j2]
            else:
                if cur:
                    gaps.append(cur)
                cur = None
        if cur:
            gaps.append(cur)
        out: list[dict[str, Any]] = []
        n_ref = len(ref)

        def omission(i1: int, i2: int, missing: int, miss_chars: int) -> None:
            at_end = i2 == n_ref
            need = p["asr_tail_min_words"] if at_end else p["asr_min_deleted_words"]
            if missing >= need and miss_chars >= p["asr_min_deleted_chars"]:
                span = (ref_toks[i1].start, ref_toks[i2 - 1].end)
                frac = (i2 - i1) / max(1, n_ref)
                kind = "truncated: the end of the text is not spoken" if at_end else "not spoken"
                out.append(
                    {
                        "category": "speech_omission",
                        "words": (i1, i2),
                        "span": span,
                        "score": damp * min(0.7, 0.35 + 0.05 * missing + (0.1 if at_end else 0.0)),
                        "severity": "critical" if frac > 0.3 else "major",
                        "evidence": f"{missing} words {kind} (text words {i1}-{i2 - 1} have no counterpart in the transcript): {text[span[0] : span[1]][:80]!r}",
                        "details": {"missing_words": missing, "at_end": at_end},
                    }
                )

        for i1, i2, j1, j2 in gaps:
            if skip and i2 > i1 and set(range(i1, i2)) <= skip:
                continue
            if 2 * (i2 - i1) <= n_ref and any(NUMERAL_RE.fullmatch(ref[k]) for k in range(i1, i2)):
                # A number read out as words fills the gap with spoken words, so counting words says nothing
                # here: a text word is missing when no spoken word in the gap resembles it (the number itself
                # absorbs any read-out). Seen in the demo: words cut just before "15.2%" went unnoticed.
                # Only for local gaps: a gap over half the text is a garbled transcript, judged as such below.
                lost = [
                    k
                    for k in range(i1, i2)
                    if not NUMERAL_RE.fullmatch(ref[k])
                    and max((similarity(ref[k], h) for h in hyp[j1:j2]), default=0.0) < 0.6
                ]
                runs: list[list[int]] = []
                for k in lost:
                    if runs and k == runs[-1][-1] + 1:
                        runs[-1].append(k)
                    else:
                        runs.append([k])
                for run in runs:
                    omission(run[0], run[-1] + 1, len(run), sum(len(ref[k]) for k in run))
                continue
            missing = (i2 - i1) - (j2 - j1)
            extra = -missing
            if missing > 0:
                miss_chars = sum(len(w) for w in ref[i1:i2]) - sum(len(w) for w in hyp[j1:j2])
                omission(i1, i2, missing, miss_chars)
            elif extra >= p["asr_min_repeated_words"]:
                ins = " ".join(hyp[j1 + (i2 - i1) : j2]) if i2 > i1 else " ".join(hyp[j1:j2])
                k = extra
                before = " ".join(ref[max(0, i1 - k) : i1])
                after = " ".join(ref[i2 : i2 + k])
                best = max(similarity(ins, before), similarity(ins, after))
                if best >= p["asr_repeat_similarity"]:
                    a_idx = max(0, i1 - k) if similarity(ins, before) >= similarity(ins, after) else i2
                    b_idx = min(n_ref, a_idx + k) - 1
                    span = (ref_toks[a_idx].start, ref_toks[max(a_idx, b_idx)].end)
                    out.append(
                        {
                            "category": "speech_repetition",
                            "words": (a_idx, b_idx + 1),
                            "span": span,
                            "score": damp * min(0.65, 0.4 + 0.05 * extra),
                            "severity": "major",
                            "evidence": f"Transcript has {extra} extra words repeating the neighbouring text ({best:.0%} similar): {text[span[0] : span[1]][:80]!r}",
                            "ngram": tuple(hyp[j1 : j1 + 3]) if extra >= 3 else None,
                        }
                    )
        # repeated trigrams that the text does not repeat (catches loops the gap logic splits up)
        ref_c = Counter(zip(ref, ref[1:], ref[2:]))
        hyp_c = Counter(zip(hyp, hyp[1:], hyp[2:]))
        for g, c in hyp_c.items():
            if c >= 2 and c > ref_c.get(g, 0) and len("".join(g)) >= 8:
                pos = next((k for k in range(len(ref) - 2) if tuple(ref[k : k + 3]) == g), None)
                if pos is None:
                    continue
                span = (ref_toks[pos].start, ref_toks[pos + 2].end)
                if any(
                    o["category"] == "speech_repetition" and o["span"][0] <= span[1] and span[0] <= o["span"][1]
                    for o in out
                ):
                    continue
                out.append(
                    {
                        "category": "speech_repetition",
                        "words": (pos, pos + 3),
                        "span": span,
                        "score": damp * 0.45,
                        "severity": "major",
                        "evidence": f"Phrase spoken {c} times but written {ref_c.get(g, 0)} time(s): {text[span[0] : span[1]][:80]!r}",
                        "ngram": g,
                    }
                )
        # A phrase spoken twice in a row that the text does not repeat, also inside numbers read out as words,
        # where the text has digits and the checks above find nothing to compare with (seen in the demo:
        # "ঊনত্রিশ কোটি ঊনত্রিশ কোটি ..." for 299,792,458). The text position is the aligned one.
        h2r = [0] * len(hyp)
        for _tag, i1, i2, j1, j2 in ops:
            for j in range(j1, j2):
                h2r[j] = min(i1 + (j - j1), max(i1, i2 - 1)) if i2 > i1 else max(0, i1 - 1)
        for n in range(2, 7):
            for j in range(len(hyp) - 2 * n + 1):
                g = tuple(hyp[j : j + n])
                if g != tuple(hyp[j + n : j + 2 * n]) or len("".join(g)) < 8 or _doubled(ref, g):
                    continue
                a_idx = min(h2r[j], n_ref - 1)
                b_idx = min(n_ref - 1, a_idx + n - 1)
                span = (ref_toks[a_idx].start, ref_toks[b_idx].end)
                if any(o["category"] == "speech_repetition" and o["span"][0] <= span[1] and span[0] <= o["span"][1] for o in out):
                    continue
                out.append(
                    {
                        "category": "speech_repetition",
                        "words": (a_idx, b_idx + 1),
                        "span": span,
                        "score": damp * 0.45,
                        "severity": "major",
                        "evidence": f"Phrase spoken twice in a row, written once: {' '.join(g)[:80]!r}",
                        "ngram": None,
                        "pair": g,
                    }
                )
        return out


def audio_path_ok(path: str | None) -> bool:
    if not path:
        return False
    return Path(path).is_file()


def _doubled(words: list[str], g: tuple[str, ...]) -> bool:
    """Is the phrase ``g`` immediately followed by itself somewhere in ``words``?"""
    n = len(g)
    return any(tuple(words[k : k + n]) == g and tuple(words[k + n : k + 2 * n]) == g for k in range(len(words) - 2 * n + 1))
