"""Transcribe audio chunks with Indic-Transcribe (hosted API), in parallel, with disk caching."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import soundfile as sf

from .api import post_with_retry
from .chunking import Chunk
from .config import BASE_URL, get_key

log = logging.getLogger(__name__)

MODEL = "indic-transcribe"
URL = f"{BASE_URL}/v1/audio/transcriptions"


def _cache_path(cache_dir: Path, audio: Path, lang: str) -> Path:
    digest = hashlib.sha256(audio.read_bytes() + lang.encode()).hexdigest()[:24]
    return cache_dir / f"asr_{digest}.json"


# --------------------------------------------------------------------------- junk replies
# Measured: some replies decay into one character repeated (a run of U+0B44 "ୄୄୄ..."), seen under loud music
# (English) and on clean TTS clips (Assamese, Urdu). It is random: 3 junk clips re-sent 17 times came back
# clean every time. So a junk reply is retried; it is never cached.
JUNK_RUN = re.compile(r"(\S)\1{7,}")   # 8+ identical non-space characters in a row; never seen in real text
JUNK_RETRIES = 2

STATS = {"junk_replies": 0, "junk_recovered": 0, "junk_unresolved": 0}
_lock = threading.Lock()


def reset_stats() -> None:
    with _lock:
        for k in STATS:
            STATS[k] = 0


def _stat(key: str) -> None:
    with _lock:
        STATS[key] += 1


def is_junk(text: str) -> bool:
    return bool(JUNK_RUN.search(text))


def strip_junk(text: str) -> str:
    """Remove the repeated-character runs; what is left is real text (the reply before the decay)."""
    return re.sub(r"\s+", " ", JUNK_RUN.sub(" ", text)).strip()


def _request(audio: Path, lang: str, key: str) -> dict:
    t0 = time.time()
    try:
        # Pass bytes, not an open file: a retry must re-send the full body.
        files = {"file": (audio.name, audio.read_bytes(), "audio/wav")}
        r = post_with_retry(URL, key, files=files, data={"model": MODEL, "language": lang})
    except Exception as e:  # network failure after all retries
        return {"ok": False, "text": "", "sec": round(time.time() - t0, 2), "error": f"{type(e).__name__}: {e}"}
    sec = round(time.time() - t0, 2)
    if not r.ok:
        return {"ok": False, "text": "", "sec": sec, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    return {"ok": True, "text": (r.json().get("text") or "").strip(), "sec": sec}


def transcribe_file(audio: Path, lang: str, key: str, cache_dir: Path | None = None) -> dict:
    """Transcribe one audio file (<= ~28 s). Returns {ok, text, cached, sec, calls, junk?, error?}.

    A junk reply is retried up to JUNK_RETRIES times. If every reply is junk, the result has junk=True and the
    text with the junk runs removed; callers decide what that means (QA: unknown; chunks: re-split)."""
    cache = _cache_path(cache_dir, audio, lang) if cache_dir else None
    if cache and cache.is_file():
        return {**json.loads(cache.read_text(encoding="utf-8")), "cached": True, "calls": 0}

    result, sec, calls = None, 0.0, 0
    for attempt in range(1 + JUNK_RETRIES):
        result = _request(audio, lang, key)
        sec += result["sec"]
        calls += 1
        if not result["ok"] or not is_junk(result["text"]):
            break
        _stat("junk_replies")
        log.warning("transcribe %s: junk reply (repeated character), attempt %d/%d: %r",
                    audio.name, attempt + 1, 1 + JUNK_RETRIES, result["text"][:60])
    result = {**result, "sec": round(sec, 2)}
    if not result["ok"]:
        return {**result, "cached": False, "calls": calls}
    if is_junk(result["text"]):
        _stat("junk_unresolved")
        return {**result, "text": strip_junk(result["text"]), "junk": True, "cached": False, "calls": calls}
    if calls > 1:
        _stat("junk_recovered")
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return {**result, "cached": False, "calls": calls}


# --------------------------------------------------------------------------- truncated replies
# Measured: ALL-CAPS replies stop at ~206-218 characters, often mid-word ("... A THIN LAYER OF SOIL T"),
# the same way on every retry; mixed-case replies were seen up to 455 characters. A reply this long is
# treated as possibly cut off: the chunk is split at a pause and each half is transcribed on its own.
CAPS_CUT = 195
ANY_CUT = 420
MIN_PIECE = 3.0   # seconds; never split into pieces shorter than this
MAX_DEPTH = 3


def looks_truncated(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    caps = sum(ch.isupper() for ch in letters) / len(letters) > 0.9
    return len(text) >= (CAPS_CUT if caps else ANY_CUT)


def split_point(regions: list[tuple[float, float]], dur: float, wav=None, sr: int = 16_000) -> float | None:
    """Where to cut a piece of length dur (times relative to the piece): the longest pause between speech
    regions in the middle half; else the pause nearest the middle; else the quietest 20 ms near the middle."""
    regs = sorted((a, b) for a, b in regions if b > a)
    gaps = [((e0 + s1) / 2, s1 - e0) for (_, e0), (s1, _) in zip(regs, regs[1:]) if s1 > e0]
    gaps = [(m, g) for m, g in gaps if MIN_PIECE <= m <= dur - MIN_PIECE]
    mid = [(m, g) for m, g in gaps if 0.25 * dur <= m <= 0.75 * dur]
    if mid:
        return max(mid, key=lambda x: x[1])[0]
    if gaps:
        return min(gaps, key=lambda x: abs(x[0] - dur / 2))[0]
    if dur < 2 * MIN_PIECE:
        return None
    if wav is None:
        return dur / 2
    win = int(0.02 * sr)
    a, b = int(0.4 * dur * sr), int(0.6 * dur * sr)
    seg = wav[a:b][: (b - a) // win * win].reshape(-1, win) if b - a >= 2 * win else None
    return dur / 2 if seg is None else (a + (int(np.argmin((seg ** 2).mean(axis=1))) + 0.5) * win) / sr


def _transcribe_split(path: Path, regions: list[tuple[float, float]], lang: str, key: str, cache_dir: Path | None,
                      depth: int = 1) -> dict:
    """Split the audio file in two at a pause and transcribe each half (recursively while still cut off)."""
    wav, sr = sf.read(str(path), dtype="float32")
    wav = wav if wav.ndim == 1 else wav.mean(axis=1)
    dur = len(wav) / sr
    cut = split_point(regions, dur, wav, sr)
    if cut is None:
        return {"ok": False}
    texts, pieces, billed = [], 0, 0.0
    for k, (t0, t1) in enumerate(((0.0, cut), (cut, dur))):
        p = path.with_name(f"{path.stem}_p{k}.wav")
        sf.write(str(p), wav[int(t0 * sr) : int(t1 * sr)], sr, subtype="PCM_16")
        sub = [(max(a, t0) - t0, min(b, t1) - t0) for a, b in regions if b > t0 and a < t1]
        r = transcribe_file(p, lang, key, cache_dir)
        if not r["ok"]:
            return {"ok": False}
        billed += (t1 - t0) * r.get("calls", 1)
        if (looks_truncated(r["text"]) or r.get("junk")) and depth < MAX_DEPTH and t1 - t0 >= 2 * MIN_PIECE:
            deeper = _transcribe_split(p, sub, lang, key, cache_dir, depth + 1)
            if deeper["ok"]:
                texts.append(deeper["text"])
                pieces += deeper["pieces"]
                billed += deeper["billed_sec"]
                continue
        texts.append(r["text"])
        pieces += 1
    return {"ok": True, "text": " ".join(t for t in texts if t), "pieces": pieces, "billed_sec": billed}


def transcribe_chunks(
    chunks: list[Chunk],
    work_dir: str | Path,
    lang: str = "en",
    concurrency: int = 4,
    cache_dir: str | Path | None = None,
) -> list[dict]:
    """Transcribe all chunks. A failed chunk is recorded with ok=False; it never aborts the run.

    Returns one dict per chunk: {id, start, end, text, ok, cached, sec, error?}.
    A reply that looks cut off is replaced by transcribing the chunk in pieces (text_unsplit keeps the
    first reply for the audit). Also writes <work_dir>/transcript_raw.json.
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(cache_dir) if cache_dir else work_dir / "cache"
    key = get_key("transcribe")

    def one(c: Chunk) -> dict:
        r = transcribe_file(Path(c.path), lang, key, cache_dir)
        r = {**r, "billed_sec": c.duration * r.get("calls", 1) if r["ok"] else 0.0}
        if r["ok"] and (looks_truncated(r["text"]) or r.get("junk")):
            why = "was junk after retries" if r.get("junk") else "looked cut off"
            rel = [(a - c.start, b - c.start) for a, b in (c.regions or [])]
            s = _transcribe_split(Path(c.path), rel, lang, key, cache_dir)
            r = {**r, "billed_sec": r["billed_sec"] + s.get("billed_sec", 0.0)}
            if s["ok"] and len(s["text"]) > len(r["text"]):
                log.warning("transcribe chunk=%d: reply %s (%d chars); re-transcribed in %d pieces "
                            "(%d -> %d words)", c.id, why, len(r["text"]), s["pieces"], len(r["text"].split()),
                            len(s["text"].split()))
                r = {**r, "text_unsplit": r["text"], "text": s["text"], "asr_pieces": s["pieces"]}
            elif r.get("junk"):
                log.warning("transcribe chunk=%d: reply was junk after retries; kept the text before the junk "
                            "(%d words)", c.id, len(r["text"].split()))
        log.info("transcribe chunk=%d dur=%.1fs ok=%s cached=%s api_sec=%.1f words=%d%s",
                 c.id, c.duration, r["ok"], r["cached"], r["sec"], len(r["text"].split()),
                 "" if r["ok"] else f" error={r.get('error')}")
        return {"id": c.id, "start": c.start, "end": c.end, "vad_start": c.vad_start, "vad_end": c.vad_end,
                "regions": c.regions, **r}

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        results = list(ex.map(one, chunks))

    (work_dir / "transcript_raw.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [r["id"] for r in results if not r["ok"]]
    billed = sum(r.get("billed_sec", 0.0) for r in results)
    log.info("transcribe: %d/%d ok, failed=%s, billed audio=%.1fs (~Rs %.2f)",
             len(results) - len(failed), len(results), failed, billed, billed / 60 * 0.10)
    return results


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    chunks_json, work = Path(sys.argv[1]), Path(sys.argv[2])
    cs = [Chunk(**d) for d in json.loads(chunks_json.read_text(encoding="utf-8"))]
    transcribe_chunks(cs, work)
