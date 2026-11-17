"""Speech checks: audio statistics (no model) and back-transcription aligned against the text that was spoken.

Day-1 facts this code is built around:
- The transcriber writes numbers as words, often English words in native script, so number tokens are wildcards.
- It invents an occasional word in clean audio, and turns pure silence or noise into a one-word "yes".
  So isolated substitutions or insertions are ignored, and garbled audio is decided from the sound itself.
- Only WAV is accepted, so everything is converted first.
"""

import difflib
import io
import re
import shutil
import subprocess
import unicodedata
import wave

import numpy as np

from .checks_text import cand
from .clients import NotRun
from .normalize import ascii_digits, normalize, protected_spans

FRAME_S = 0.02
CHUNK_MAX_S = 28.0  # under the documented 30 s window, with margin
CHUNK_MIN_S = 18.0  # cut at the quietest frame between these two points
LONG_SILENCE_S = 2.0  # clean speech pauses measured on day 1 were at most 0.54 s
CLIP_SHARE = 0.005  # share of samples at full scale; clean speech peaked well below full scale
MIN_RUN_WORDS = 3  # a run of missing words shorter than this is treated as transcriber noise
PLACEHOLDER = "∅"  # stands for a number or formula: spoken as words we cannot predict


# ---------------------------------------------------------------- audio I/O

def to_wav(data: bytes, name: str = "audio") -> bytes:
    """Return PCM16 mono WAV bytes; converts anything else with ffmpeg."""
    if not data:
        raise NotRun("input: audio file is empty")
    try:
        with wave.open(io.BytesIO(data)) as w:
            if w.getsampwidth() == 2 and w.getnchannels() == 1 and w.getnframes() > 0:
                return data
    except (wave.Error, EOFError):
        pass
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise NotRun(f"input: {name} is not 16-bit mono WAV and ffmpeg is not installed")
    p = subprocess.run([ffmpeg, "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", "-f", "wav", "pipe:1"],
                       input=data, capture_output=True, timeout=120)
    if p.returncode or len(p.stdout) < 100:
        raise NotRun(f"input: {name} is not readable audio")
    return _fix_wav_header(p.stdout)


def _fix_wav_header(data: bytes) -> bytes:
    # ffmpeg writing to a pipe cannot seek back to fill in sizes; rewrite them.
    with wave.open(io.BytesIO(data)) as w:
        params, frames = w.getparams(), w.readframes(w.getnframes() or 10**9)
    return wav_bytes(np.frombuffer(frames, "<i2").astype(np.float32) / 32768.0, params.framerate)


def read_samples(wav: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(wav)) as w:
        sr, frames = w.getframerate(), w.readframes(w.getnframes())
    return np.frombuffer(frames, "<i2").astype(np.float32) / 32768.0, sr


def wav_bytes(x: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


# ---------------------------------------------------------------- audio statistics

def frame_rms(x: np.ndarray, sr: int) -> np.ndarray:
    n = max(1, int(sr * FRAME_S))
    usable = len(x) // n * n
    if usable == 0:
        return np.zeros(1, np.float32)
    return np.sqrt((x[:usable].reshape(-1, n) ** 2).mean(axis=1))


def audio_stats(x: np.ndarray, sr: int) -> dict:
    rms = frame_rms(x, sr)
    peak_rms = float(rms.max())
    floor = max(0.02 * peak_rms, 1e-3)
    voiced = np.flatnonzero(rms > floor)
    stats = {"duration_s": round(len(x) / sr, 3), "peak": round(float(np.abs(x).max()) if len(x) else 0.0, 4),
             "clipped_share": round(float((np.abs(x) >= 0.999).mean()) if len(x) else 0.0, 5), "peak_rms": round(peak_rms, 5)}
    if voiced.size == 0:
        return {**stats, "voiced_s": 0.0, "lead_s": stats["duration_s"], "trail_s": 0.0, "longest_silence_s": 0.0, "silence_runs": [],
                "contrast": 0.0, "end_energy": 0.0}
    inner = rms[voiced[0]:voiced[-1] + 1]
    silent = inner <= floor
    runs, start = [], None
    for i, s in enumerate(silent):
        if s and start is None:
            start = i
        elif not s and start is not None:
            runs.append((start, i))
            start = None
    runs_s = [((voiced[0] + a) * FRAME_S, (voiced[0] + b) * FRAME_S) for a, b in runs]
    longest = max((b - a for a, b in runs_s), default=0.0)
    loud = np.sort(inner)
    contrast = float(loud[int(0.1 * (len(loud) - 1))] / (loud[int(0.9 * (len(loud) - 1))] or 1))
    tail = rms[voiced[-1] - 2:voiced[-1] + 1] if voiced[-1] >= 2 else rms[voiced[-1]:voiced[-1] + 1]
    return {**stats, "voiced_s": round((voiced[-1] - voiced[0] + 1) * FRAME_S, 3), "lead_s": round(voiced[0] * FRAME_S, 3),
            "trail_s": round((len(rms) - 1 - voiced[-1]) * FRAME_S, 3), "longest_silence_s": round(longest, 3),
            "silence_runs": [(round(a, 2), round(b, 2)) for a, b in runs_s if b - a >= LONG_SILENCE_S],
            "contrast": round(contrast, 3), "end_energy": round(float(tail.mean() / (np.median(rms[voiced]) or 1)), 3)}


def stats_candidates(st: dict) -> list[dict]:
    """Garbled audio from the sound alone. Exact where clean speech cannot produce the value."""
    c = []
    if st["peak_rms"] < 0.003:
        c.append(cand("audio_stats", "garbled", "exact", 0.95, severity="major", detail="audio is silent", audio_span_s=[0, st["duration_s"]]))
        return c
    if st["clipped_share"] >= CLIP_SHARE:
        c.append(cand("audio_stats", "garbled", "exact", 0.9, severity="major", detail="audio is clipped", clipped_share=st["clipped_share"]))
    for a, b in st["silence_runs"]:
        c.append(cand("audio_stats", "garbled", "exact", 0.85, severity="major", detail=f"{b - a:.1f} s of silence inside the speech",
                      audio_span_s=[a, b]))
    if st["contrast"] >= 0.6:  # speech has quiet gaps between syllables; steady noise does not
        c.append(cand("audio_stats", "garbled", "noisy", 0.6, severity="major", detail="no quiet gaps: noise or unintelligible audio",
                      contrast=st["contrast"]))
    for x in c:
        x["audio_span_s"] = x["evidence"].pop("audio_span_s", None)
    return c


# ---------------------------------------------------------------- text that was spoken

def spoken_tokens(text: str) -> list[tuple[str, tuple[int, int]]]:
    """Words of the text as spoken, with spans. Numbers and formulas become placeholders, since their spoken form is unpredictable."""
    digits = ascii_digits(text)
    chars = list(digits)
    for s, e, kind in protected_spans(text):
        if kind in ("url", "html"):
            for i in range(s, e):
                chars[i] = " "
        else:  # a formula or code is read aloud in words we cannot predict: one placeholder token
            for i in range(s, e):
                chars[i] = "0"
    masked = "".join(chars)
    out = []
    for m in re.finditer(r"[^\s]+", _strip_punct(masked)):
        tok = normalize(m.group())[0]
        if any(ch.isdigit() for ch in tok):
            tok = PLACEHOLDER
        out.append((tok, (m.start(), m.end())))
    merged = []  # consecutive placeholders are one unpredictable stretch
    for tok, span in out:
        if tok == PLACEHOLDER and merged and merged[-1][0] == PLACEHOLDER:
            merged[-1] = (PLACEHOLDER, (merged[-1][1][0], span[1]))
        else:
            merged.append((tok, span))
    return merged


def _strip_punct(text: str) -> str:
    return "".join(" " if unicodedata.category(ch)[0] in "PS" else ch for ch in text)


def transcript_tokens(text: str) -> list[str]:
    return [normalize(t)[0] for t in _strip_punct(ascii_digits(text)).split()]


# ---------------------------------------------------------------- transcription

def chunks(x: np.ndarray, sr: int) -> list[tuple[int, int]]:
    """Cut long audio at the quietest frame between CHUNK_MIN_S and CHUNK_MAX_S."""
    out, start, n = [], 0, len(x)
    while n - start > CHUNK_MAX_S * sr:
        lo, hi = start + int(CHUNK_MIN_S * sr), start + int(CHUNK_MAX_S * sr)
        rms = frame_rms(x[lo:hi], sr)
        quietest_last = len(rms) - 1 - int(np.argmin(rms[::-1]))  # latest of equally quiet frames: fewer chunks
        cut = lo + quietest_last * int(sr * FRAME_S)
        out.append((start, cut))
        start = cut
    out.append((start, n))
    return out


def transcribe_long(x: np.ndarray, sr: int, lang: str, bodhan, ctx=None) -> tuple[str, list[dict]]:
    pieces, info = [], []
    for a, b in chunks(x, sr):
        text = bodhan.transcribe(wav_bytes(x[a:b], sr), lang, ctx=ctx)
        pieces.append(text)
        info.append({"start_s": round(a / sr, 2), "end_s": round(b / sr, 2), "words": len(text.split())})
    return " ".join(p for p in pieces if p), info


RECHECK_PAD_S = 1.5  # seconds around the estimated position: speaking rate varies within an utterance
RECHECK_HEARD = 0.6  # share of the missing words heard in the cut for the omission to count as checker noise


def recheck_omission(x: np.ndarray, sr: int, text: str, finding: dict, lang: str, bodhan, ctx=None) -> float:
    """Transcribe only the stretch where the missing words should be (position estimated from the character offset,
    since the API returns no timestamps). Returns the share of the missing words heard there."""
    s, e = finding["span"]
    dur = len(x) / sr
    t0 = max(0.0, s / max(1, len(text)) * dur - RECHECK_PAD_S)
    t1 = min(dur, e / max(1, len(text)) * dur + RECHECK_PAD_S)
    piece = bodhan.transcribe(wav_bytes(x[int(t0 * sr):int(t1 * sr)], sr), lang, ctx=ctx)
    heard = set(transcript_tokens(piece))
    missing = finding["missing"]
    return sum(w in heard for w in missing) / max(1, len(missing))


# ---------------------------------------------------------------- alignment

def _is_repeat(inserted: list[str], context: list[str]) -> bool:
    """The inserted words repeat words that are spoken right next to them."""
    if len(inserted) < 2:
        return False
    joined = " ".join(inserted)
    return joined in " ".join(context) or difflib.SequenceMatcher(a=inserted, b=context[:len(inserted)]).ratio() >= 0.8 \
        or difflib.SequenceMatcher(a=inserted, b=context[-len(inserted):]).ratio() >= 0.8


def align(text: str, transcript: str) -> dict:
    """Word alignment of spoken text and transcript, classified by the shape of each mismatch."""
    toks = spoken_tokens(text)
    a = [t for t, _ in toks]
    b = transcript_tokens(transcript)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    words = [t for t in a if t != PLACEHOLDER]
    findings = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        missing = [t for t in a[i1:i2] if t != PLACEHOLDER]
        extra = b[j1:j2]
        if all(t == PLACEHOLDER for t in a[i1:i2]) and i2 > i1:
            continue  # a number or formula read aloud: any words are acceptable here
        span = (toks[i1][1][0], toks[i2 - 1][1][1]) if i2 > i1 else None
        at_end = all(t == PLACEHOLDER for t in a[i2:])
        if len(missing) >= MIN_RUN_WORDS and len(missing) > len(extra) + 1:
            findings.append({"shape": "truncation" if at_end else "omission", "span": span, "missing": missing})
        elif len(extra) > len(missing) + 1 and _is_repeat(extra, b[max(0, j1 - len(extra)):j1] + b[j2:j2 + len(extra)]):
            pos = toks[i1 - 1][1] if i1 else toks[0][1]
            findings.append({"shape": "repetition", "span": span or pos, "repeated": extra})
    # Transcript words aligned to a number or formula in the text: a number read aloud twice ("2023" written twice) is
    # not a loop, so n-grams touching these are skipped.
    readings = {j for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal" and PLACEHOLDER in a[i1:i2] for j in range(j1, j2)}
    counts = {}
    for n in range(3, 6):  # a phrase spoken more often than it is written: loops that the diff absorbed
        for k in range(len(b) - n + 1):
            if not readings.intersection(range(k, k + n)):
                g = tuple(b[k:k + n])
                counts[g] = counts.get(g, 0) + 1
    text_str = f" {' '.join(a)} "
    loops = [g for g, c in counts.items() if c > 1 and c > text_str.count(f" {' '.join(g)} ") and PLACEHOLDER not in g]
    if loops and not any(f["shape"] == "repetition" for f in findings):
        findings.append({"shape": "repetition", "span": None, "repeated": list(max(loops, key=len))})
    ratio = difflib.SequenceMatcher(a=words, b=b, autojunk=False).ratio() if words else 1.0
    return {"similarity": round(ratio, 4), "findings": findings, "text_words": len(words), "transcript_words": len(b)}


LONG_TAIL_SHARE = 1 / 3  # chosen while looking at the demo data; see the build journal


def align_candidates(al: dict, st: dict) -> list[dict]:
    c = []
    for f in al["findings"]:
        if f["shape"] == "truncation":
            # ASR drops a few words, not a third of an utterance: a tail that long is the speech being cut off.
            long_tail = len(f["missing"]) >= LONG_TAIL_SHARE * max(1, al["text_words"])
            c.append(cand("transcript", "speech_omission", "exact" if long_tail else "noisy", 0.9 if long_tail else 0.7, None, f["span"],
                          "major", detail="end of the text not spoken (truncation)", missing=" ".join(f["missing"])[:200]))
        elif f["shape"] == "omission":
            c.append(cand("transcript", "speech_omission", "noisy", 0.6, None, f["span"], "major", detail="words not spoken",
                          missing=" ".join(f["missing"])[:200]))
        else:
            c.append(cand("transcript", "speech_repetition", "noisy", 0.6, None, f["span"], "major", detail="phrase spoken twice",
                          repeated=" ".join(f["repeated"])[:200]))
    dur = st.get("voiced_s", 0)
    if dur > 2 and al["text_words"] >= 4 and al["transcript_words"] / dur < 0.5:
        c.append(cand("transcript", "garbled", "noisy", 0.7, None, None, "major", detail="almost nothing intelligible was transcribed",
                      transcript_words=al["transcript_words"], seconds=dur))
    return c


def speaking_rate_candidates(rows: list[tuple[int, float, bool]], z_fn, min_items: int, z_cut: float) -> dict[int, list[dict]]:
    """rows: (index, seconds per character, has_text). Weak support for truncation (too short) and repetition or silence (too long)."""
    usable = [(i, r) for i, r, ok in rows if ok and r > 0]
    if len(usable) < min_items:
        return {}
    out = {}
    for (i, _), z in zip(usable, z_fn([np.log(r) for _, r in usable]), strict=True):
        if z <= -z_cut:
            out[i] = [cand("audio_stats", "speech_omission", "weak", min(1.0, -z / 6), detail="audio much shorter than the text needs", z=round(z, 2))]
        elif z >= z_cut:
            out[i] = [cand("audio_stats", "speech_repetition", "weak", min(1.0, z / 6), detail="audio much longer than the text needs", z=round(z, 2))]
    return out


def spoken_chars(text: str) -> int:
    return sum(1 for t, _ in spoken_tokens(text) for ch in t) or 0
