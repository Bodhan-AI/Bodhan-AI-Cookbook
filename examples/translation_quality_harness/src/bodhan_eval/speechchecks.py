"""Speech checks: signal analysis of the waveform plus alignment of the Indic-Transcribe
back-transcript against the text that was spoken.

Checker noise vs system error: ASR mistakes are scattered, similar-length substitutions;
real omissions and repetitions are contiguous, length-changing gaps. Only the latter
become candidates, and their minimum size is a calibrated threshold, not a guess.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from difflib import SequenceMatcher

import numpy as np
import soundfile as sf

from bodhan_eval.flags import Candidate
from bodhan_eval.normalize import cer, normalize_with_map, script_of

FRAME_S = 0.02


def load_mono(data: bytes) -> tuple[np.ndarray, int]:
    samples, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    return samples.mean(axis=1), sr


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) index runs where mask is True."""
    if not len(mask):
        return []
    padded = np.concatenate([[False], mask, [False]]).astype(int)
    edges = np.flatnonzero(np.diff(padded))
    return list(zip(edges[::2], edges[1::2], strict=True))


def analyze_audio(data: bytes) -> dict:
    """Duration, silences, clipping and noise-like (spectrally flat) stretches."""
    x, sr = load_mono(data)
    frame = max(1, int(FRAME_S * sr))
    n = len(x) // frame
    if n == 0:
        return {"duration_s": len(x) / sr if sr else 0.0, "speech_s": 0.0, "empty": True}
    frames = x[: n * frame].reshape(n, frame)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    silent = rms < max(1e-3, 0.06 * float(np.percentile(rms, 95)))
    voiced_idx = np.flatnonzero(~silent)
    if not len(voiced_idx):
        return {"duration_s": len(x) / sr, "speech_s": 0.0, "empty": True}
    first, last = int(voiced_idx[0]), int(voiced_idx[-1])
    internal = [(s, e) for s, e in _runs(silent) if s > first and e <= last]
    longest_gap = max(internal, key=lambda r: r[1] - r[0], default=(0, 0))

    spectrum = np.abs(np.fft.rfft(frames * np.hanning(frame), axis=1)) ** 2 + 1e-12
    flatness = np.exp(np.log(spectrum).mean(axis=1)) / spectrum.mean(axis=1)
    noisy = (~silent) & (flatness > 0.5)
    longest_noise = max(_runs(noisy), key=lambda r: r[1] - r[0], default=(0, 0))

    return {
        "duration_s": round(len(x) / sr, 3),
        "speech_start_s": round(first * FRAME_S, 3),
        "speech_end_s": round((last + 1) * FRAME_S, 3),
        "speech_s": round((last + 1 - first) * FRAME_S, 3),
        "longest_internal_silence_s": round(float(longest_gap[1] - longest_gap[0]) * FRAME_S, 3),
        "longest_internal_silence_at_s": round(float(longest_gap[0]) * FRAME_S, 3),
        "longest_noise_s": round(float(longest_noise[1] - longest_noise[0]) * FRAME_S, 3),
        "longest_noise_at_s": round(float(longest_noise[0]) * FRAME_S, 3),
        "clipped_ratio": round(float(np.mean(np.abs(x) >= 0.999)), 5),
        "empty": False,
    }


def _time_to_span(t0: float, t1: float, audio: dict, text_len: int) -> tuple[int, int]:
    """Approximate text span for a stretch of audio, assuming a constant speaking rate."""
    start, dur = audio["speech_start_s"], max(audio["speech_s"], 1e-6)
    a = int(np.clip((t0 - start) / dur, 0, 1) * text_len)
    b = int(np.clip((t1 - start) / dur, 0, 1) * text_len)
    return (a, max(b, a + 1))


def audio_candidates(audio: dict, text: str) -> list[Candidate]:
    n = len(text)
    if audio.get("empty") or audio.get("speech_s", 0) < 0.3:
        return [Candidate("speech_omission", 1.0, "audio_signal", "critical", (0, n), evidence={"problem": "no audible speech", **audio})]
    out = []
    gap = audio["longest_internal_silence_s"]
    if gap >= 1.0:
        t0 = audio["longest_internal_silence_at_s"]
        out.append(Candidate("speech_garbled", min(1.0, gap / 3.0), "audio_signal", "major", _time_to_span(t0, t0 + gap, audio, n),
                             evidence={"problem": f"{gap:.2f}s silence inside speech", "at_s": t0}))
    noise = audio["longest_noise_s"]
    if noise >= 0.2:
        t0 = audio["longest_noise_at_s"]
        out.append(Candidate("speech_garbled", min(1.0, noise / 1.0), "audio_signal", "major", _time_to_span(t0, t0 + noise, audio, n),
                             evidence={"problem": f"{noise:.2f}s of noise-like audio", "at_s": t0}))
    if audio["clipped_ratio"] > 0.005:
        out.append(Candidate("speech_garbled", min(1.0, audio["clipped_ratio"] * 50), "audio_signal", "minor", (0, n),
                             evidence={"problem": "clipping", "clipped_ratio": audio["clipped_ratio"]}))
    return out


def speech_rate_outliers(rows: list[tuple[str, str, float]]) -> dict[str, list[Candidate]]:
    """Duration sanity check. rows: (key, text, speech seconds) for one language. Speaking rate far
    above the batch median means content was skipped; far below means loops, pauses or noise."""
    rates = {k: len(normalize_with_map(t)[0].replace(" ", "")) / s for k, t, s in rows if s > 0.3 and t.strip()}
    if len(rates) < 8:
        return {}
    log_rates = {k: float(np.log(r)) for k, r in rates.items()}
    med = float(np.median(list(log_rates.values())))
    mad = float(np.median([abs(v - med) for v in log_rates.values()])) or 0.05
    out = {}
    for k, v in log_rates.items():
        z = (v - med) / (1.4826 * mad)
        if abs(z) >= 3:
            cat, problem = ("speech_omission", "speech too short for the text") if z > 0 else ("speech_garbled", "speech too long for the text")
            out[k] = [Candidate(cat, min(1.0, abs(z) / 10), "speech_rate", "major", None, evidence={
                "problem": problem, "chars_per_second": round(rates[k], 2), "language_median": round(float(np.exp(med)), 2), "robust_z": round(z, 2)})]
    return out


def _loops(ref, hyp, raw_span, text_len: int) -> list[Candidate]:
    """Loops the alignment absorbs: a phrase heard more often than it is written. Longest first.
    3-word phrases must exist in the text (short phrases repeat naturally); 4+ word phrases may be
    spoken-only (math or numbers read aloud and then looped)."""
    ref_w, hyp_w = [w for w, _, _ in ref], [w for w, _, _ in hyp]
    for n in (6, 5, 4, 3):
        ref_counts = Counter(tuple(ref_w[k:k + n]) for k in range(len(ref_w) - n + 1))
        hyp_counts = Counter(tuple(hyp_w[k:k + n]) for k in range(len(hyp_w) - n + 1))
        for gram, count in sorted(hyp_counts.items()):
            if count < 2 or count <= ref_counts.get(gram, 0) or (n == 3 and gram not in ref_counts):
                continue
            if gram in ref_counts:
                k = next(i for i in range(len(ref_w) - n + 1) if tuple(ref_w[i:i + n]) == gram)
                span = raw_span(k, k + n)
            else:  # spoken-only phrase: place it proportionally at its second occurrence
                second = [i for i in range(len(hyp_w) - n + 1) if tuple(hyp_w[i:i + n]) == gram][1]
                a = int(second / max(1, len(hyp_w)) * text_len)
                span = (a, min(text_len, a + max(1, int(n / max(1, len(hyp_w)) * text_len))))
            return [Candidate("speech_repetition", min(1.0, 0.3 + 0.1 * n), "back_transcription", "major", span,
                              evidence={"phrase": " ".join(gram), "heard_times": count, "written_times": ref_counts.get(gram, 0)})]
    return []


def _words(norm: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in re.finditer(r"\S+", norm)]


def transcript_candidates(text: str, transcript: str) -> tuple[list[Candidate], dict]:
    """Align the spoken text with its back-transcript at word level (normalized)."""
    norm, offsets = normalize_with_map(text)
    ref = _words(norm)
    hyp = _words(normalize_with_map(transcript)[0])
    metrics = {"cer": round(cer(text, transcript), 4), "ref_words": len(ref), "hyp_words": len(hyp)}
    if not ref:
        return [], metrics

    def raw_span(i: int, j: int) -> tuple[int, int]:
        """Raw-text span covering normalized ref words [i, j)."""
        if i >= len(ref):
            return (len(text), len(text))
        start = offsets[ref[i][1]]
        end = offsets[ref[max(i, j - 1)][2] - 1] + 1
        return (start, end)

    native_text = any(script_of(c) not in (None, "LATIN") for c in norm)

    def spoken_differently(word: str) -> bool:
        """Digits and Latin/math tokens are read aloud as words ("100" -> "वन हंड्रेड", "x" -> "எக்ஸ்"):
        their written and spoken forms are not comparable as text."""
        return any(ch.isdigit() for ch in word) or (native_text and any(script_of(ch) == "LATIN" for ch in word))

    def similar(a: str, b: str, cutoff: float) -> bool:
        return a == b or SequenceMatcher(a=a, b=b).ratio() >= cutoff

    comparable_chars = sum(len(w) for w, _, _ in ref if not spoken_differently(w))
    out = []
    sm = SequenceMatcher(a=[w for w, _, _ in ref], b=[w for w, _, _ in hyp], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        # Inside a differing block, digit/Latin words are set aside (spoken as words), and each remaining
        # written word is paired with a similar heard word (ASR spelling noise). What stays unpaired is a gap.
        free_hyp = list(range(j1, j2))
        missing = []
        for k in range(i1, i2):
            if spoken_differently(ref[k][0]):
                continue
            pair = next((j for j in free_hyp if similar(ref[k][0], hyp[j][0], 0.5)), None)
            if pair is None:
                missing.append(k)
            else:
                free_hyp.remove(pair)
        missing_chars = sum(len(ref[k][0]) for k in missing)
        if missing_chars >= 4:
            at_end = i2 == len(ref) and j2 == len(hyp)
            span = raw_span(min(missing), max(missing) + 1)
            # Absolute size of the gap, plus the share of the utterance it removes, plus truncation at the end.
            share = missing_chars / max(1, comparable_chars)
            score = min(1.0, missing_chars / 30 + 0.5 * share + (0.1 if at_end else 0.0))
            out.append(Candidate("speech_omission", score, "back_transcription", "major", span, evidence={
                "not_heard": text[slice(*span)], "heard_instead": " ".join(w for w, _, _ in hyp[j1:j2]),
                "missing_chars": missing_chars, "truncated_end": at_end,
            }))

        extra = [hyp[j][0] for j in free_hyp]
        if sum(len(w) for w in extra) < 4:
            continue
        # Repetition: unpaired heard words that were already written just before (or right after).
        lo = max(0, i1 - len(extra) - 3)
        window = [w for w, _, _ in ref[lo: i2 + len(extra) + 3]]
        repeated = [w for w in extra if any(similar(w, r, 0.75) for r in window)]
        repeated_chars = sum(len(w) for w in repeated)
        if repeated_chars >= 6 and len(repeated) >= 2:
            first = next(k for k, r in enumerate(window) if similar(repeated[0], r, 0.75))
            out.append(Candidate("speech_repetition", min(1.0, 0.3 + repeated_chars / 30), "back_transcription", "major",
                                 raw_span(lo + first, lo + first + len(repeated)),
                                 evidence={"repeated_text": " ".join(repeated), "heard": " ".join(w for w, _, _ in hyp[j1:j2])}))
        elif not any(spoken_differently(ref[k][0]) for k in range(i1, i2)):
            # Measured precision 0 on its own (ASR hallucination noise), so it can only corroborate a signal-level finding.
            out.append(Candidate("speech_garbled", min(0.3, sum(len(w) for w in extra) / 60), "back_transcription", "minor",
                                 raw_span(i1, i2) if i1 < i2 else raw_span(max(0, i1 - 1), i1), evidence={"unexpected_speech": " ".join(extra)}))

    if not any(c.category == "speech_repetition" for c in out):
        out += _loops(ref, hyp, raw_span, len(text))

    # Whole-utterance check on comparable words only (raw CER is inflated by spoken numbers and math).
    comparable = [w for w, _, _ in ref if not spoken_differently(w)]
    heard = {w for w, _, _ in hyp}
    if len(comparable) >= 3:
        found = sum(1 for w in comparable if w in heard or any(SequenceMatcher(a=w, b=h).ratio() >= 0.75 for h in heard))
        coverage = found / len(comparable)
        metrics["word_coverage"] = round(coverage, 3)
        if coverage < 0.5 and not out:
            out.append(Candidate("speech_garbled", min(1.0, 1.0 - coverage), "back_transcription", "major", (0, len(text)), evidence={
                "problem": "back-transcript barely matches the text", "word_coverage": round(coverage, 3), "cer": metrics["cer"]}))
    return out, metrics
