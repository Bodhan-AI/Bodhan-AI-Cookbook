"""Zero-cost checks of the tools against test_data: nothing here calls a Bodhan API.
Run: uv run python scripts/offline_data_checks.py"""
import asyncio
import collections
import os
import tempfile
from pathlib import Path

DATA = Path("test_data").resolve()
os.environ["BODHAN_WORKDIR"] = tempfile.mkdtemp()
os.environ["BODHAN_INPUT_DIRS"] = str(DATA)
for k in ("TRANSCRIBE", "TRANSLATE", "SPEAK", "OCR"):
    os.environ[f"BODHAN_{k}_KEY"] = "offline"  # never used: every check stops before a network call
os.environ["BODHAN_BASE_URL"] = "http://127.0.0.1:9"  # even a stray call cannot reach the real API

from bodhan_agent import safety  # noqa: E402
from bodhan_mcp import core  # noqa: E402
from bodhan_mcp.core import ToolError  # noqa: E402
from bodhan_mcp.languages import detect_language  # noqa: E402
from bodhan_mcp.tools import audio, ocr, translate  # noqa: E402

rows = []


def row(area, item, result, ok=True):
    rows.append((area, item, result, ok))
    print(f"{'PASS' if ok else 'FAIL'} | {area:<10} | {item:<44} | {result}")


def code(fn, *a, **kw):
    try:
        r = fn(*a, **kw)
        if asyncio.iscoroutine(r):
            r = asyncio.run(r)
        return "ok", r
    except ToolError as e:
        return e.code, None
    except Exception as e:
        return f"CRASH {type(e).__name__}", None


# 1. inspect_media on every media/text file: classify, never crash
kinds = collections.Counter()
for f in sorted(DATA.rglob("*")):
    if f.is_file() and f.suffix not in (".json", ".gt.txt") and not f.name.endswith(".gt.txt") and f.parent.name not in ("hi_in",) or f.suffix in (".wav",):
        c, r = code(audio.inspect_media, str(f))
        kinds[(f.suffix, r["kind"] if r else c)] += 1
crashes = {k: v for k, v in kinds.items() if str(k[1]).startswith("CRASH")}
row("inspect", "all files classified, no crash", f"{sum(kinds.values())} files; crashes={crashes or 0}", not crashes)
for f, expect in [("audio/formats/zero_byte.wav", "UNSUPPORTED_FORMAT"), ("audio/formats/not_audio.wav", "UNSUPPORTED_FORMAT"),
                  ("video/no_audio_track.mp4", "UNSUPPORTED_FORMAT"), ("pdf/encrypted.pdf", "UNSUPPORTED_FORMAT")]:
    c, _ = code(audio.inspect_media, str(DATA / f))
    row("inspect", f, c, c == expect)

# 2. audio chunk plans on the real files (silencedetect + planner, no API)
for f in ["audio/hi_3min.wav", "audio/hi_long.wav", "audio/formats/silence_30s.wav", "audio/formats/noisy.wav",
          "audio/formats/telephone_8k.wav", "audio/formats/sample.m4a", "audio/formats/sample.webm"]:
    src = DATA / f
    d = audio.inspect_media(str(src))["duration_s"]
    tmp = Path(os.environ["BODHAN_WORKDIR"]) / "t.wav"
    audio._to_wav(src, tmp)
    sil = audio._run(["ffmpeg", "-i", str(tmp), "-af", "silencedetect=noise=-35dB:d=0.3", "-f", "null", "-"])
    cuts = audio.plan_cuts(d, audio.parse_silences(sil.stderr))
    mx = max(b - a for a, b in cuts)
    row("chunking", f, f"{d:.0f}s -> {len(cuts)} chunks, longest {mx:.1f}s", mx <= audio.MAX_CHUNK_S + 1e-6)

# 3. OCR rendering of every image/PDF (no API): what can we even send?
for f in sorted((DATA / "ocr").rglob("*")):
    if f.suffix.lower() in (".png", ".jpg", ".heic") and not f.name.endswith(".gt.txt"):
        c, r = code(ocr.render, f, None, 200)
        if r:
            from PIL import Image
            import io
            im = Image.open(io.BytesIO(r[0][1]))
            ex = Image.open(f).getexif().get(0x0112) if f.suffix.lower() != ".heic" else None
            raw = Image.open(f).size
            swapped = ex in (5, 6, 7, 8)
            applied = (im.size == raw[::-1]) if swapped else True  # rotation tags 5-8 swap width and height
            note = f"{im.size}" + (f" EXIF orientation={ex} applied={applied}" if ex and ex != 1 else "")
            row("ocr-render", f.relative_to(DATA / "ocr").as_posix(), note, applied)
        else:
            row("ocr-render", f.relative_to(DATA / "ocr").as_posix(), c, False)
for f, expect in [("pdf/pages_62.pdf", "TOO_LARGE"), ("pdf/encrypted.pdf", "UNSUPPORTED_FORMAT")]:
    c, _ = code(ocr.render, DATA / f, None, 100)
    row("ocr-render", f, c, c == expect)
for f, n in [("pdf/scanned_chapter_hi.pdf", 8), ("pdf/scanned_mixed_languages_4p.pdf", 4), ("pdf/digital_text_layer.pdf", 1)]:
    c, r = code(ocr.render, DATA / f, None, 100)
    row("ocr-render", f, f"{len(r) if r else c} pages", bool(r) and len(r) == n)

# 4. validation errors that must fire before any paid call
for label, fn, kw, expect in [
    ("transcribe language=fr", audio.transcribe_audio, dict(path=str(DATA / "audio/fr_fr" / min(p.name for p in (DATA / "audio/fr_fr").glob("*.wav"))), language="fr"), "UNSUPPORTED_LANGUAGE"),
    ("translate target=fr", translate.translate_text, dict(text="hello", target_language="fr"), "UNSUPPORTED_LANGUAGE"),
    ("translate target=Bhojpuri", translate.translate_text, dict(text="hello", target_language="bhojpuri"), "UNSUPPORTED_LANGUAGE"),
    ("translate empty.txt", translate.translate_text, dict(text_ref=str(DATA / "text/empty.txt"), target_language="ta"), "EMPTY_INPUT"),
    ("transcribe a PDF", audio.transcribe_audio, dict(path=str(DATA / "pdf/pages_62.pdf")), "UNSUPPORTED_FORMAT"),
    ("path outside sandbox", audio.inspect_media, dict(path="/etc/hosts"), "PATH_NOT_ALLOWED"),
]:
    c, _ = code(fn, **kw)
    row("validation", label, c, c == expect)

# 5. language-ID heuristic vs FLEURS reference text, per language (no API)
expect = {"hi_in": "hi", "mr_in": "mr", "ne_np": "ne", "ta_in": "ta", "te_in": "te", "bn_in": "bn", "kn_in": "kn",
          "ml_in": "ml", "or_in": "or", "gu_in": "gu", "pa_in": "pa", "ur_pk": "ur", "as_in": "as", "fr_fr": None, "en_us": "en"}
for d, lang in expect.items():
    txts = [p.read_text(encoding="utf-8") for p in sorted((DATA / "audio" / d).glob("*.txt"))]
    hits = [detect_language(t)["language"] for t in txts]
    ok = sum(h == lang for h in hits)
    row("lang-id", f"{d} text -> {lang}", f"{ok}/{len(txts)} correct {hits if ok < len(txts) else ''}", ok == len(txts) if lang else True)
for f, lang in [("text/long_hi_in.txt", "hi"), ("text/long_mr_in.txt", "mr"), ("text/long_ta_in.txt", "ta"), ("text/long_en_us.txt", "en")]:
    g = detect_language((DATA / f).read_text(encoding="utf-8"))
    row("lang-id", f, f"{g['language']} conf={g['confidence']}", g["language"] == lang)

# 6. injection detector on the crafted files
for f, should in [("text/injection_en.txt", True), ("text/injection_hi.txt", True), ("text/long_en_us.txt", False)]:
    hits = safety.injection_hits((DATA / f).read_text(encoding="utf-8"))
    row("injection", f, f"flagged={bool(hits)}", bool(hits) == should)

bad = [r for r in rows if not r[3]]
print(f"\n{len(rows) - len(bad)}/{len(rows)} passed; {len(bad)} failed")
