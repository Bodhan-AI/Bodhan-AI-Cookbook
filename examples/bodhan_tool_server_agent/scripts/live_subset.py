"""Live tool-level checks against real Bodhan APIs on the open test data (costs credits, about Rs 4).
No spending cap on purpose: a low balance surfaces as INSUFFICIENT_CREDITS and the run stops there.
Run: uv run python scripts/live_subset.py   (cache on: reruns are free for finished items)"""
import asyncio
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(".env")
DATA = Path("test_data").resolve()
os.environ["BODHAN_WORKDIR"] = str(Path(".bodhan_work").resolve())
os.environ["BODHAN_INPUT_DIRS"] = str(DATA)
os.environ.pop("BODHAN_MAX_COST_INR", None)

from bodhan_agent.verify import _norm, check_translation, tokens
from bodhan_agent.verify import similarity as _similarity
from bodhan_mcp import core
from bodhan_mcp.core import ToolError
from bodhan_mcp.tools import audio, ocr, speak, translate

OUT = Path("docs/LIVE_RESULTS.md")
lines = ["# Live results", "", f"Run {time.strftime('%Y-%m-%d %H:%M')}; real Bodhan APIs; open test data. Similarity = character-trigram F1 vs reference text (1.0 = identical); wer/cer = word/character error rate vs the dataset's reference text (wer is inflated where the reference splits compound words differently; cer is the fairer figure for Indic scripts). Costs are what the ledger recorded (cached items read Rs 0).", "",
         "| Check | Result | Detail | Rs |", "|---|---|---|---|"]
STOP = False


LAST_WER = None  # (wer, cer) of the pair judged last


def wer(ref_: str, hyp: str, chars: bool = False) -> float:
    """Word (or character) error rate: edit distance over the reference length."""
    r, h = (list(_norm(ref_)), list(_norm(hyp))) if chars else (tokens(ref_), tokens(hyp))
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[len(h)] / max(1, len(r))


def similarity(a: str, b: str) -> float:  # also remember the WER of the pair being judged, for the report row
    global LAST_WER
    LAST_WER = (wer(a, b), wer(a, b, chars=True))
    return _similarity(a, b)


def ref(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def log(name, ok, detail, cost=0.0):
    global LAST_WER
    if LAST_WER is not None and "sim" in detail:
        detail += f", wer={LAST_WER[0]:.1%}, cer={LAST_WER[1]:.1%}"
    LAST_WER = None
    lines.append(f"| {name} | {'PASS' if ok else 'FAIL' if ok is False else 'INFO'} | {detail} | {cost:.3f} |")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(("PASS" if ok else "FAIL" if ok is False else "INFO"), "|", name, "|", detail, "|", f"Rs {cost:.3f}", flush=True)


async def step(name, coro, judge):
    global STOP
    if STOP:
        return None
    await asyncio.sleep(2)  # stay under the API's per-minute rate limit
    try:
        r = await coro
        ok, detail = judge(r)
        log(name, ok, detail, r.get("cost_inr", 0) if isinstance(r, dict) else 0)
        return r
    except ToolError as e:
        if e.code == "INSUFFICIENT_CREDITS":
            STOP = True
            log(name, None, f"STOPPED: {e.message}")
        else:
            log(name, None, f"{e.code}: {e.message[:140]}")
    except Exception as e:
        log(name, False, f"CRASH {type(e).__name__}: {e}")


def txt(r, key):
    return Path(r[key]).read_text(encoding="utf-8")


async def main():
    start = core.spent()
    A = DATA / "audio"
    # 1. long audio: chunking + accuracy (reference = the clips joined in the same order)
    hi = sorted((A / "hi_in").glob("*.wav"))
    acc, used = 0.0, []
    for f in hi:
        used.append(f)
        acc += audio.inspect_media(str(f))["duration_s"]
        if acc >= 185:
            break
    await step("transcribe hi_3min.wav (unhinted)", audio.transcribe_audio(str(A / "hi_3min.wav")),
                   lambda r: (r["chunks"] >= 7 and r["language"] == "hi" and not r["warnings"],
                              f"{r['chunks']} chunks, lang={r['language']} ({r['language_method']}), sim={similarity(' '.join(ref(f.with_suffix('.txt')) for f in used), txt(r, 'text_ref')):.2f}, warnings={r['warnings']}"))
    # 2. near-limit clip + language detection without a hint, one clip per language
    ta = max((A / "ta_in").glob("*.wav"), key=lambda p: p.stat().st_size)
    await step("transcribe ta 25 s clip (unhinted)", audio.transcribe_audio(str(ta)),
               lambda r: (r["language"] == "ta" and r["chunks"] == 1, f"lang={r['language']}, chunks={r['chunks']}, sim={similarity(ref(ta.with_suffix('.txt')), txt(r, 'text_ref')):.2f}"))
    for d, lang in [("te_in", "te"), ("kn_in", "kn"), ("ml_in", "ml"), ("mr_in", "mr"), ("ne_np", "ne"), ("bn_in", "bn"), ("as_in", "as")]:
        f = min((A / d).glob("*.wav"))
        await step(f"language ID {d}", audio.transcribe_audio(str(f)),
                   lambda r, f=f, lang=lang: (r["language"] == lang, f"detected={r['language']} conf={r['language_confidence']} alts={r['language_alternatives']}, sim={similarity(ref(f.with_suffix('.txt')), txt(r, 'text_ref')):.2f}"))
    f = min((A / "fr_fr").glob("*.wav"))
    await step("French audio (unsupported language)", audio.transcribe_audio(str(f)),
               lambda r: (None, f"model returned lang={r['language']}, text starts: {txt(r, 'text_ref')[:60]!r}"))
    # 3. formats and video
    for n in ["sample.mp3", "sample.ogg", "sample.flac", "sample.m4a", "sample.aac", "sample.webm", "stereo_44k.wav", "telephone_8k.wav", "noisy.wav"]:
        await step(f"format {n}", audio.transcribe_audio(str(A / "formats" / n), language="hi"),
                   lambda r: (not r["warnings"] and len(txt(r, "text_ref")) > 10, f"sim={similarity(ref(min((A / 'hi_in').glob('*.txt'))), txt(r, 'text_ref')):.2f}"))
    await step("video te_speech.mp4", audio.transcribe_audio(str(DATA / "video/te_speech.mp4")),
               lambda r: (r["language"] == "te", f"lang={r['language']}, {r['duration_s']}s"))
    # 4. translation: long text, chunking, truncation, script
    longen = DATA / "text/long_en_us.txt"
    t = await step("translate long English -> ta", translate.translate_text(text_ref=str(longen), target_language="ta"),
                   lambda r: (check_translation(ref(longen), txt(r, "translation_ref"), "ta", "plain", r["warnings"])["ok"],
                              f"chars {r['chars_in']}->{r['chars_out']}, resplit={r['resplit_pieces']}, warnings={r['warnings']}"))
    await step("translate Hindi text -> English", translate.translate_text(text_ref=str(DATA / "text/long_hi_in.txt"), target_language="en"),
               lambda r: (r["source_language_detected"] == "hi" and not r["warnings"], f"src={r['source_language_detected']}, chars {r['chars_in']}->{r['chars_out']}"))
    # 5. TTS round trip on three translated sentences
    if t:
        short = txt(t, "translation_ref")[:300]  # three sentences' worth; NEVER the whole text (TTS is Rs 6 per 10k chars)
        assert len(short) <= 400
        s = await step("speak ta (3 sentences)", speak.synthesize_speech(text=short, language="ta", gender="male"),
                       lambda r: (True, f"{r['chunks']} chunks, {r['duration_s']}s, voice={r['voice']}"))
        if s:
            await step("back-transcribe the speech", audio.transcribe_audio(s["audio_ref"], language="ta"),
                       lambda r: (similarity(short, txt(r, "text_ref")) >= 0.6, f"similarity={similarity(short, txt(r, 'text_ref')):.2f}"))
    # 6. OCR accuracy on real documents
    O = DATA / "ocr"
    for n in ["Hindi_1", "Tamil_1", "Kannada_1"]:
        await step(f"OCR {n}", ocr.ocr_document(str(O / f"{n}.png")),
                   lambda r, n=n: (similarity(ref(O / f"{n}.gt.txt"), txt(r, "markdown_ref")) >= 0.6, f"similarity={similarity(ref(O / f'{n}.gt.txt'), txt(r, 'markdown_ref')):.2f}, conf={r['mean_confidence']}"))
    # 7. tables and phone photos
    rp = O / "real_photos/guwahati_ropeway_fares_timetable.jpg"
    o = await step("OCR real iPhone photo (table notice)", ocr.ocr_document(str(rp)),
                   lambda r: ("|" in txt(r, "markdown_ref"), f"{r['chars']} chars, table pipes={txt(r, 'markdown_ref').count('|')}, conf={r['mean_confidence']}"))
    if o:
        await step("translate that table -> mr (markdown)", translate.translate_text(text_ref=o["markdown_ref"], target_language="mr", mode="markdown"),
                   lambda r: (not r["warnings"], f"warnings={r['warnings']}"))
    await step("OCR multi-script sign photo", ocr.ocr_document(str(O / "real_photos/train_name_kannada_hindi_tamil_english.jpg")),
               lambda r: (r["chars"] > 10, f"{r['chars']} chars: {txt(r, 'markdown_ref')[:90]!r}"))
    await step("OCR real table Hindi_table3", ocr.ocr_document(str(O / "tables/Hindi_table3.png")),
               lambda r: ("|" in txt(r, "markdown_ref"), f"table pipes={txt(r, 'markdown_ref').count('|')}, sim={similarity(ref(O / 'tables/Hindi_table3.gt.txt'), txt(r, 'markdown_ref')):.2f}"))
    for n in ["exif_rotated.jpg", "photo.heic", "rotated_90.png"]:
        await step(f"OCR variant {n}", ocr.ocr_document(str(O / "variants" / n)),
                   lambda r, n=n: (similarity(ref(O / "Hindi_1.gt.txt"), txt(r, "markdown_ref")) >= 0.5, f"sim vs Hindi_1 ground truth={similarity(ref(O / 'Hindi_1.gt.txt'), txt(r, 'markdown_ref')):.2f}"))
    # 8. multi-page PDF (4 pages) keeps page order
    await step("OCR 4-page scanned PDF", ocr.ocr_document(str(DATA / "pdf/scanned_mixed_languages_4p.pdf")),
               lambda r: (r["pages_done"] == 4 and not r["pages_failed"], f"pages {r['pages_done']}/{r['pages_total']}, failed={r['pages_failed']}"))
    log("TOTAL spent by this run (ledger delta)", None, f"Rs {core.spent() - start:.3f}")


asyncio.run(main())
