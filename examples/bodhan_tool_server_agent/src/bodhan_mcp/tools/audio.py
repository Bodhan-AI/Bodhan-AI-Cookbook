"""inspect_media, transcribe_audio (chunked), subtitle building."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import wave
from pathlib import Path

from .. import core
from ..core import PRICES, ToolError, register_heif
from ..languages import ASR, detect_language, norm

MAX_CHUNK_S = 28.0  # API limit is 30 s; 2 s margin
MIN_CHUNK_S = 5.0
AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".webm", ".amr", ".opus", ".wma"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif"}


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    if not shutil.which(cmd[0]):
        raise ToolError("INTERNAL", f"{cmd[0]} is not installed", "Install ffmpeg (brew/apt install ffmpeg).")
    return subprocess.run(cmd, capture_output=True, text=True)


def probe(path: Path) -> dict:
    r = _run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)])
    if r.returncode != 0:
        raise ToolError("UNSUPPORTED_FORMAT", f"cannot read media: {path.name}", "File may be corrupt or not media.")
    return json.loads(r.stdout)


def inspect_media(path: str) -> dict:
    p = core.resolve_in(path)
    ext = p.suffix.lower()
    info: dict = {"path": str(p), "bytes": p.stat().st_size, "notes": []}
    if ext == ".pdf":
        import pymupdf as fitz
        try:
            with fitz.open(p) as d:
                if d.needs_pass:
                    raise ToolError("UNSUPPORTED_FORMAT", f"{p.name} is password-protected", "Provide an unprotected copy.")
                info |= {"kind": "pdf", "pages": d.page_count}
        except Exception as e:
            raise ToolError("UNSUPPORTED_FORMAT", f"cannot open PDF: {e}", "File may be corrupt or encrypted.")
    elif ext in IMAGE_EXT:
        from PIL import Image
        register_heif()
        try:
            with Image.open(p) as im:
                info |= {"kind": "image", "width": im.width, "height": im.height}
        except Exception as e:
            raise ToolError("UNSUPPORTED_FORMAT", f"cannot open image: {e}")
    elif ext in AUDIO_EXT | VIDEO_EXT:
        j = probe(p)
        streams = j.get("streams", [])
        a = next((s for s in streams if s.get("codec_type") == "audio"), None)
        has_video = any(s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1 for s in streams)
        if not a:
            raise ToolError("UNSUPPORTED_FORMAT", f"{p.name} has no audio stream")
        try:
            dur = round(float(j["format"].get("duration", 0)), 2)
        except (TypeError, ValueError):
            dur = 0.0  # "N/A": transcribe_audio measures the decoded WAV instead
        info |= {"kind": "video" if has_video else "audio", "duration_s": dur,
                 "sample_rate": int(a.get("sample_rate", 0)), "channels": a.get("channels"), "codec": a.get("codec_name")}
        if has_video:
            info["notes"].append("video: audio track can be transcribed; no video output is possible")
    else:
        head = p.read_bytes()[:65536]
        if b"\x00" in head and head[:2] not in (b"\xff\xfe", b"\xfe\xff"):  # binary: maybe media with an unusual extension
            r = _run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(p)])
            j = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
            a = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), None)
            if a:
                try:
                    dur = round(float(j["format"].get("duration", 0)), 2)
                except (TypeError, ValueError):
                    dur = 0.0
                return info | {"kind": "audio", "duration_s": dur, "sample_rate": int(a.get("sample_rate", 0)),
                               "channels": a.get("channels"), "codec": a.get("codec_name")}
            info["kind"] = "unknown"
            info["notes"].append("not media or text")
        else:
            text = core.read_text(p)
            enc = "utf-16" if head[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig" if head[:3] == b"\xef\xbb\xbf" else "utf-8"
            info |= {"kind": "text", "chars": len(text), "text_encoding": enc,
                     "dominant_script": detect_language(text)["script"]}
    return info


# ---------- chunking ----------
def parse_silences(stderr: str) -> list[tuple[float, float]]:
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", stderr)]
    return list(zip(starts, ends))


def quietest(wav: Path, a: float, b: float, frame_s: float = 0.05) -> float | None:
    """Time of the quietest 50 ms frame between a and b seconds: the least harmful place to cut when there is no silence."""
    import array
    with wave.open(str(wav)) as w:
        rate = w.getframerate()
        w.setpos(max(0, min(w.getnframes(), int(a * rate))))
        samples = array.array("h", w.readframes(max(0, int((b - a) * rate))))
    n = max(1, int(frame_s * rate))
    best, at = None, None
    for i in range(0, len(samples) - n + 1, n):
        e = sum(x * x for x in samples[i:i + n])
        if best is None or e <= best:  # ties go to the later frame, so chunks stay long
            best, at = e, i
    return None if at is None else round(a + (at + n / 2) / rate, 3)


def plan_cuts(duration: float, silences: list[tuple[float, float]], max_s: float = MAX_CHUNK_S,
              min_s: float = MIN_CHUNK_S, quiet=None) -> list[tuple[float, float]]:
    """Cut points at silence midpoints so no chunk exceeds max_s. Where a window holds no silence (noisy audio) `quiet(a, b)`
    gives the quietest point in the last 40 percent of the window instead of a hard cut mid-word. Pure function when `quiet` is None."""
    mids = sorted((a + b) / 2 for a, b in silences)
    out, start = [], 0.0
    while duration - start > max_s:
        cands = [m for m in mids if start + min_s <= m <= start + max_s]
        end = max(cands) if cands else (quiet(start + max(min_s, 0.6 * max_s), start + max_s) if quiet else None) or start + max_s
        out.append((round(start, 3), round(end, 3)))
        start = end
    if out and duration - start < 1.0:  # a sliver of a chunk is useless and the API may reject it: merge into the last one
        out[-1] = (out[-1][0], round(duration, 3))
    else:
        out.append((round(start, 3), round(duration, 3)))
    return out


def split_cues(text: str, start: float, end: float, max_cue_s: float = 7.0) -> list[dict]:
    """Break one chunk's text into cues; time is shared by character count (model returns no timestamps)."""
    dur = end - start
    if dur <= max_cue_s or not text.strip():
        return [{"start": start, "end": end, "text": text.strip()}]
    parts = [s.strip() for s in re.split(r"(?<=[।.?!॥])\s+", text) if s.strip()] or [text.strip()]
    n_cues = max(1, round(dur / max_cue_s))
    if len(parts) < n_cues:  # no sentence punctuation (the model returns none): split on words instead
        words = text.split()
        k = max(1, -(-len(words) // n_cues))
        parts = [" ".join(words[i:i + k]) for i in range(0, len(words), k)]
    target = max(1, len(text) // n_cues)
    groups, cur = [], ""
    for s in parts:
        if cur and len(cur) + len(s) > target:
            groups.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        groups.append(cur)
    total, t, cues = sum(len(g) for g in groups) or 1, start, []
    for g in groups:
        d = dur * len(g) / total
        cues.append({"start": round(t, 3), "end": round(t + d, 3), "text": g})
        t += d
    return cues


def repetitive(text: str, n: int = 5, limit: int = 3) -> bool:
    """True if any run of n words occurs `limit`+ times (the model looping on a phrase). A speaker may repeat a sentence
    twice; a loop repeats it again and again."""
    w = text.split()
    if len(w) < n * limit:
        return False
    grams: dict[tuple, int] = {}
    for i in range(len(w) - n + 1):
        g = tuple(w[i:i + n])
        grams[g] = grams.get(g, 0) + 1
    return max(grams.values()) >= limit


def _trim_silence(src: Path, dst: Path) -> None:
    """Drop leading/trailing silence: long silent tails are what makes the model loop."""
    af = "silenceremove=start_periods=1:start_threshold=-40dB:stop_periods=1:stop_duration=0.4:stop_threshold=-40dB"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-af", af, "-ar", "16000", "-ac", "1", str(dst)])


def _to_wav(src: Path, dst: Path, start: float | None = None, length: float | None = None) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if start is not None:
        cmd += ["-ss", f"{start}", "-t", f"{length}"]
    cmd += ["-i", str(src), "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(dst)]
    r = _run(cmd)
    if r.returncode != 0:
        raise ToolError("UNSUPPORTED_FORMAT", f"ffmpeg could not decode {src.name}: {r.stderr[-200:]}")


async def _transcribe_chunk(wav: Path, lang: str | None) -> tuple[str, float, bool]:
    data = wav.read_bytes()
    key = core.key_of("transcribe", lang or "", data)
    cached = core.cache_get(key)
    if cached is None and lang:  # the same audio was already sent without a hint: reuse that text if it is in this language
        free = core.cache_get(core.key_of("transcribe", "", data))
        if free is not None and detect_language(json.loads(free)["text"])["language"] == lang:
            cached = free
            core.cache_put(key, free)
    if cached is not None:
        return json.loads(cached)["text"], 0.0, True
    text, est, hit = await core.once(key, lambda: _transcribe_new(wav, data, key, lang))
    return text, est, hit


async def _transcribe_new(wav: Path, data: bytes, key: str, lang: str | None) -> tuple[str, float, bool]:
    secs = len(data) / 32000  # 16 kHz mono s16
    est = secs / 60 * PRICES["transcribe_per_min"]
    core.reserve(est)
    try:
        form = {"model": "indic-transcribe"} | ({"language": lang} if lang else {})
        r = await core.post("transcribe", "/v1/audio/transcriptions", data=form,
                            files={"file": (wav.name, data, "audio/wav")})
        text = r.json().get("text", "")
        core.record("transcribe", est, wav.name)
    finally:
        core.release(est)
    core.cache_put(key, json.dumps({"text": text}).encode())
    return text, est, False


async def transcribe_audio(path: str, language: str | None = None, timestamps: bool = True) -> dict:
    src = core.resolve_in(path)
    lang = None
    if language:
        lang = norm(language)
        if lang not in ASR:
            raise ToolError("UNSUPPORTED_LANGUAGE", f"'{language}' is not supported by indic-transcribe",
                            f"Supported: {', '.join(sorted(ASR))}")
    info = inspect_media(path)
    if info["kind"] not in ("audio", "video"):
        raise ToolError("UNSUPPORTED_FORMAT", f"{src.name} is a {info['kind']}, not audio/video",
                        "Use ocr_document for images/PDFs.")
    dur = info["duration_s"]

    tmp = core.workdir() / "tmp"
    tmp.mkdir(exist_ok=True)
    stem = core.key_of(str(src), src.stat().st_mtime_ns)[:12]
    full = tmp / f"{stem}.wav"
    await asyncio.to_thread(_to_wav, src, full)
    with wave.open(str(full)) as w:  # the decoded file is the truth; container metadata can be missing or wrong
        dur = round(w.getnframes() / w.getframerate(), 2)
    if dur <= 0:
        raise ToolError("EMPTY_INPUT", "audio has zero duration")
    sil = await asyncio.to_thread(_run, ["ffmpeg", "-i", str(full), "-af", "silencedetect=noise=-35dB:d=0.3", "-f", "null", "-"])
    cuts = await asyncio.to_thread(plan_cuts, dur, parse_silences(sil.stderr), MAX_CHUNK_S, MIN_CHUNK_S,
                                   lambda a, b: quietest(full, a, b))
    parts = []
    for i, (a, b) in enumerate(cuts):
        p = tmp / f"{stem}_{i}.wav"
        await asyncio.to_thread(_to_wav, full, p, a, b - a)
        parts.append(p)

    warnings, cost, cached_n = [], 0.0, 0
    # first chunk unhinted when language unknown; reuse detected language for the rest
    texts: list[str] = [""] * len(parts)
    t0, c0, h0 = await _transcribe_chunk(parts[0], lang)
    texts[0], cost, cached_n = t0, c0, int(h0)
    method = "given" if lang else "none"
    conf = 1.0 if lang else 0.0
    if not lang:
        d = detect_language(t0)
        # a hint from one chunk is passed to all the others: only when the detection is sure (no look-alike language such as
        # Hindi/Marathi); otherwise every chunk is detected by the API on its own, so one wrong guess cannot spread
        if d["language"] and d["confidence"] >= 0.6 and d["language"] in ASR and not d["alternatives"]:
            lang, method, conf = d["language"], d["method"], d["confidence"]
    rest = await asyncio.gather(*[_transcribe_chunk(p, lang) for p in parts[1:]], return_exceptions=True)
    skipped: list[int] = []  # chunks the budget could not pay for: keep the prefix we have, say so
    for i, r in enumerate(rest, 1):
        if isinstance(r, ToolError) and r.code == "BUDGET_EXCEEDED":
            skipped.append(i)
        elif isinstance(r, BaseException):
            raise r
        else:
            texts[i], cost, cached_n = r[0], cost + r[1], cached_n + int(r[2])
    if skipped:
        done_s = cuts[min(skipped) - 1][1]
        warnings.append(f"BUDGET EXHAUSTED: transcribed only the first {done_s:.0f} s of {dur:.0f} s "
                        f"(chunks {min(skipped)}-{len(parts) - 1} not done); raise the cost cap to finish")
    for i, t in enumerate(texts):
        if i in skipped:
            continue
        if t.strip() and repetitive(t):  # one repair attempt: same audio without silent edges
            trimmed = parts[i].with_name(parts[i].stem + "_trim.wav")
            await asyncio.to_thread(_trim_silence, parts[i], trimmed)
            try:
                t2, c2, h2 = await _transcribe_chunk(trimmed, lang)
            except ToolError as e:
                if e.code != "BUDGET_EXCEEDED":
                    raise
                t2, c2, h2 = "", 0.0, True
            cost, cached_n = cost + c2, cached_n + int(h2)
            if t2.strip() and not repetitive(t2) and len(t2) < len(t):
                warnings.append(f"chunk {i}: repeated phrase detected, fixed by re-transcribing without silent edges")
                texts[i] = t2
                continue
        if not texts[i].strip():
            warnings.append(f"chunk {i} ({cuts[i][0]}-{cuts[i][1]}s) returned empty text")
        elif repetitive(texts[i]):
            warnings.append(f"chunk {i} still looks repetitive after a retry; transcript may contain a repeated phrase")
    full_text = "\n".join(t.strip() for t in texts if t.strip())
    det = detect_language(full_text) if full_text else {"language": lang, "confidence": 0, "alternatives": []}
    if not lang:
        lang, method, conf = det["language"], det["method"], det["confidence"]
    if method != "given" and lang == "en" and full_text.strip():
        warnings.append("Latin-script transcript and no language was given: the audio may be English, or a language "
                        "Indic-Transcribe does not support (it then returns plausible-looking nonsense instead of refusing). "
                        "Treat the text as unverified unless the audio is known to be English.")
    segs = []
    for (a, b), t in zip(cuts, texts):
        segs += split_cues(t, a, b) if timestamps else [{"start": a, "end": b, "text": t.strip()}]
    segs = [s for s in segs if s["text"]]
    name = f"transcript_{stem}"
    tref = core.write_text(f"{name}.txt", full_text)
    sref = core.write_text(f"{name}.segments.json", json.dumps(segs, ensure_ascii=False, indent=1))
    return {"language": lang, "language_confidence": conf, "language_method": method,
            "language_alternatives": det.get("alternatives", []), "text_ref": str(tref),
            "segments_ref": str(sref), "preview": core.preview(full_text), "duration_s": dur,
            "chunks": len(parts), "cached_chunks": cached_n, "cost_inr": round(cost, 4), "warnings": warnings}


# ---------- SRT ----------
def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _wrap(text: str, width: int = 42) -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def build_srt(segments_ref: str, out_name: str = "subtitles.srt") -> dict:
    segs = json.loads(core.read_text(core.resolve_in(segments_ref)))
    if not isinstance(segs, list) or not segs:
        raise ToolError("EMPTY_INPUT", "segments file has no cues")
    out, prev_end = [], 0.0
    for i, s in enumerate(segs, 1):
        if not s.get("text", "").strip():
            continue
        a = max(float(s["start"]), prev_end)
        b = max(float(s["end"]), a + 0.5)
        prev_end = b
        out.append(f"{len(out) + 1}\n{_ts(a)} --> {_ts(b)}\n{_wrap(s['text'].strip())}\n")
    if not out:
        raise ToolError("EMPTY_INPUT", "all cues are empty")
    p = core.write_text(out_name if out_name.endswith(".srt") else out_name + ".srt", "\n".join(out))
    return {"srt_ref": str(p), "cues": len(out)}
