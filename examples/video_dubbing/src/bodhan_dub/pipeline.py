"""The dubbing pipeline: shared English analysis once, then one dubbing run per language (in parallel).

Stages: probe -> extract -> VAD chunks -> transcribe -> sentence grouping (LLM) -> glossary (LLM) -> speakers
Per language: translate -> speak -> fit ladder -> back-transcribe QA -> retry -> mix -> mux -> subtitles -> report
"""
import hashlib
import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from . import audio, config, onscreen, qa, segmentation, textutil, timing
from .bodhan import BodhanClient, BodhanError, Meter
from .llm import Brain
from .logs import log

MAX_CONDENSE = 2
MAX_TTS_RETRIES = 2


@dataclass
class Options:
    input: Path
    langs: list[str]
    out: Path
    glossary: Path | None = None
    voices: Path | None = None
    code_mixed: bool = False
    concurrency: int = 4
    src_lang: str = "en"
    style: str | None = None
    force: bool = False
    ocr: bool = False            # translate on-screen text with Indic-OCR
    ocr_interval: float = 2.0    # seconds between sampled frames
    ocr_max_frames: int = 40     # upper bound on sampled frames (so on Indic-OCR calls)
    merge: bool = False          # partial re-run: keep other languages' entries in report.json
    extra: dict = field(default_factory=dict)


# ------------------------------------------------------------------ English analysis (shared)
def file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(1 << 22):
            h.update(b)
    return h.hexdigest()


def analyse_source(opt: Options, client: BodhanClient, brain: Brain, work: Path) -> dict:
    t0 = time.time()
    sha = file_sha(opt.input)
    cache = work / "source.json"
    if cache.exists() and not opt.force:
        src = json.loads(cache.read_text())
        if src.get("sha") == sha and src.get("version") == 2:
            log("stage", stage="source", cached=True)
            return src
    duration = audio.probe_duration(opt.input)
    log("stage", stage="probe", duration=round(duration, 2))
    warnings = []
    if not 55 <= duration <= 15 * 60 + 5:
        warnings.append(f"duration {duration:.0f}s is outside the 1-15 minute range the pipeline is tuned for")
    if not audio.has_audio(opt.input):
        raise RuntimeError("input has no audio track")
    wav = work / "audio16k.wav"
    audio.extract_audio(opt.input, wav)
    spans = audio.vad_spans(wav, duration)
    chunks, skipped = segmentation.make_chunks(spans, duration)
    log("stage", stage="vad", spans=len(spans), chunks=len(chunks), skipped=len(skipped))

    (work / "chunks").mkdir(exist_ok=True)

    def do(i):
        p = work / "chunks" / f"{i}.wav"
        audio.cut(wav, chunks[i]["start"], chunks[i]["end"], p)
        return client.transcribe(p, opt.src_lang, chunks[i]["end"] - chunks[i]["start"])

    with ThreadPoolExecutor(opt.concurrency) as ex:
        texts = list(ex.map(do, range(len(chunks))))
    keep = []
    for i, t in enumerate(texts):
        bad = "no_speech_transcribed" if not t.strip() else segmentation.implausible_transcript(
            t, chunks[i]["end"] - chunks[i]["start"])
        if bad:  # silence / music / hallucinated text: never invent narration
            skipped.append({"start": chunks[i]["start"], "end": chunks[i]["end"], "reason": bad})
        else:
            keep.append(i)
    chunks, texts = [chunks[i] for i in keep], [texts[i] for i in keep]
    log("stage", stage="transcribe", chunks=len(chunks))

    groups, how = brain.group_sentences(texts, chunks)
    segs = segmentation.build_segments(texts, chunks, groups)
    log("stage", stage="group_sentences", segments=len(segs), method=how)
    if not brain.available:
        warnings.append("LLM not configured (set LLM_BASE_URL, LLM_MODEL and LLM_API_KEY): using rule-based sentence grouping")
    elif how != "llm":
        warnings.append(f"LLM call failed, used rule-based grouping: {brain.last_error or 'unknown error'}")

    glossary = textutil.merge_glossary(textutil.load_glossary(opt.glossary),
                                       brain.build_glossary(" ".join(s["text"] for s in segs)))
    log("stage", stage="glossary", terms=len(glossary), llm=brain.available)

    f0s = [audio.median_f0(wav, s["start"], s["end"]) for s in segs]
    spk = audio.assign_speakers(f0s)
    for s, sp in zip(segs, spk):
        s.update(speaker=sp["speaker"], gender=sp["gender"], f0=sp["f0"])
    gaps = segmentation.gaps_for(segs, duration)
    for s, (b, a) in zip(segs, gaps):
        s["gap_before"], s["gap_after"] = round(b, 3), round(a, 3)

    src = {"version": 2, "sha": sha, "duration": duration, "segments": segs, "skipped": skipped,
           "glossary": glossary, "grouping": how, "warnings": warnings, "seconds": round(time.time() - t0, 2),
           "speakers": sorted({s["speaker"] for s in segs})}
    textutil.atomic_write(cache, json.dumps(src, ensure_ascii=False, indent=1))
    return src


# ------------------------------------------------------------------ per-language dubbing
def pick_voice(voices: dict, lang: str, speaker: str, gender: str, speakers_in_gender: list[str] | None = None) -> str:
    """voices.yaml wins (per speaker, then per language/gender, then global); else the language's own recorded voice."""
    v = (voices.get("speakers") or {}).get(speaker)
    if v:
        return v
    lv = (voices.get("languages") or {}).get(lang) or {}
    if lv.get(gender) or voices.get(gender):
        return lv.get(gender) or voices.get(gender)
    options = config.VOICES.get(lang, {}).get(gender) or [config.DEFAULT_VOICES[gender]]
    k = (speakers_in_gender or [speaker]).index(speaker) if speaker in (speakers_in_gender or [speaker]) else 0
    return options[k % len(options)]


def speak_pieces(client: BodhanClient, text: str, voice: str, lang: str, style, attempt: int, nonce: int) -> bytes:
    """Short pieces keep TTS stable; retries use finer pieces so a failure is not simply repeated."""
    max_chars = (220, 120, 70)[min(attempt, 2)]
    pieces = textutil.split_for_tts(text, max_chars) or [text]
    parts = [client.speak(p, voice, lang, style, nonce=nonce + attempt) for p in pieces]
    return parts[0] if len(parts) == 1 else audio.concat_wavs(parts)


def back_transcribe(client: BodhanClient, clip: Path, lang: str, work: Path) -> str:
    dur = audio.probe_duration(clip)
    tmp = work / f"{clip.stem}.qa.wav"
    audio.to_16k(clip, tmp)
    if dur <= 28:
        return client.transcribe(tmp, lang, dur)
    out, n = [], int(dur // 25) + 1
    for k in range(n):
        p = work / f"{clip.stem}.qa{k}.wav"
        audio.cut(tmp, k * dur / n, (k + 1) * dur / n, p)
        out.append(client.transcribe(p, lang, dur / n))
    return " ".join(out)


class LangRun:
    def __init__(self, opt: Options, lang: str, client: BodhanClient, brain: Brain, src: dict, voices: dict):
        self.opt, self.lang, self.client, self.brain, self.src, self.voices = opt, lang, client, brain, src, voices
        self.dir = opt.out / lang
        self.work = opt.out / "work" / lang
        self.work.mkdir(parents=True, exist_ok=True)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.glossary = src["glossary"]
        self.canon: dict[str, str] = {}
        ov = self.dir / "overrides.json"
        self.overrides = json.loads(ov.read_text()) if ov.exists() else {}
        self.script = "codemix" if (opt.code_mixed and lang not in config.NO_CODEMIX) else "native"

    def same_gender(self, seg: dict) -> list[str]:
        return sorted({s["speaker"] for s in self.src["segments"] if s["gender"] == seg["gender"]})

    # ---- translation with glossary
    def translate(self, english: str) -> tuple[str, list[str]]:
        notes = []
        src_text, used = textutil.apply_user_translations(english, self.glossary, self.lang)
        res = self.client.translate(src_text, self.lang, self.script)
        if res["truncated"]:
            notes.append("translation_truncated")
            half = [self.client.translate(p, self.lang, self.script)["text"]
                    for p in textutil.split_for_tts(src_text, max(40, len(src_text) // 2))]
            res = {"text": " ".join(half), "truncated": False}
        text = res["text"]
        for g in textutil.terms_in(english, self.glossary):
            if g["term"] in used or not g.get("pin"):
                continue
            canon = self.canon.get(g["term"])
            if canon is None:
                canon = self.canon[g["term"]] = self.client.translate(g["term"], self.lang, self.script)["text"].strip(" .।")
            if canon and canon not in text:
                notes.append(f"term_variant:{g['term']}")
        for n in qa.numbers_preserved(english, text):
            notes.append(f"number_missing:{n}")
        return text, notes

    # ---- one segment
    def segment(self, seg: dict, nxt_start: float) -> tuple[dict, Path | None]:
        i, ov = seg["idx"], self.overrides.get(str(seg["idx"]), {})
        english = ov.get("english") or seg["text"]
        voice = ov.get("voice") or pick_voice(self.voices, self.lang, seg["speaker"], seg["gender"], self.same_gender(seg))
        style = ov.get("style") or self.opt.style
        if style == "none":                      # the editor's explicit "no style" choice beats the project style
            style = None
        max_stretch = min(float(ov.get("max_stretch", config.STRETCH_MAX)), config.STRETCH_MAX)
        slot = seg["end"] - seg["start"]
        rec = {"idx": i, "start": seg["start"], "end": seg["end"], "speaker": seg["speaker"], "gender": seg["gender"],
               "voice": voice, "style": style, "source_text": seg["text"], "english_used": english, "retries": {"condense": 0, "tts": 0},
               "notes": [], "status": "failed", "attempts": []}
        clip = self.work / f"seg{i}.wav"
        translation = ov.get("text")
        notes = rec["notes"]
        try:
            custom = self.dir / ov["custom_audio"] if ov.get("custom_audio") else None
            if custom is not None and custom.exists():
                if not translation:          # the text only feeds the subtitles here, so a failed translation must not drop the line
                    try:
                        translation, tn = self.translate(english)
                        notes += tn
                    except BodhanError as e:
                        translation = ""
                        notes.append(f"subtitle_translation_failed:{str(e)[:80]}")
                else:
                    notes.append("manual_text")
                return self.custom_segment(rec, seg, nxt_start, ov, custom, clip, translation), clip
            if translation:
                notes.append("manual_text")
            else:
                translation, tn = self.translate(english)
                notes += tn
            best = None
            for cond in range(MAX_CONDENSE + 1):
                best = None
                for attempt in range(MAX_TTS_RETRIES + 1):
                    raw = speak_pieces(self.client, translation, voice, self.lang, style, attempt, nonce=cond * 10)
                    d = audio.wav_duration(raw)
                    plan = timing.plan_fit(d, slot, seg["gap_after"], seg["gap_before"], max_stretch)
                    if ov.get("speed"):   # manual speed from the editor, still clamped to the allowed range
                        sp = min(config.STRETCH_MAX, max(config.STRETCH_MIN, float(ov["speed"])))
                        plan = timing.FitPlan("manual_speed", sp, 0.0, 0.0)
                    dur = audio.fit_clip(raw, plan.stretch, clip)
                    back = back_transcribe(self.client, clip, self.lang, self.work)
                    q = qa.assess(translation, back)
                    if q["ok"] and 0.2 < q["cer"] and self.brain.available:
                        j = self.brain.judge(english, translation, back)
                        if j is False:
                            q["flags"].append("llm_judged_bad")
                            q["ok"] = False
                    rec["attempts"].append({"cond": cond, "tts": attempt, "raw_s": round(d, 2), "action": plan.action,
                                            "stretch": round(plan.stretch, 3), "cer": q["cer"], "flags": q["flags"]})
                    log("segment_attempt", lang=self.lang, idx=i, **rec["attempts"][-1])
                    cand = (q["ok"], -q["cer"], plan, dur, q, back, clip.read_bytes())
                    if best is None or cand[:2] > best[:2]:
                        best = cand
                    if q["ok"]:
                        break
                    rec["retries"]["tts"] += 1
                ok, _, plan, dur, q, back, data = best
                if plan.action != "condense" or cond == MAX_CONDENSE or ov.get("text"):
                    break
                short = self.brain.condense(english, timing.condense_ratio(d, slot, seg["gap_after"], seg["gap_before"]),
                                            [g["term"] for g in textutil.terms_in(english, self.glossary)])
                if not short:
                    notes.append("condense_unavailable")
                    break
                rec["retries"]["condense"] += 1
                english = short
                rec["english_used"] = short
                translation, tn = self.translate(short)
                notes += tn
                log("condensed", lang=self.lang, idx=i, english=short)
            _, _, plan, dur, q, back, data = best
            clip.write_bytes(data)
            start = seg["start"] + plan.start_shift
            rec.update(translation=translation, audio_duration=round(dur, 3), action=plan.action,
                       stretch=round(plan.stretch, 3), placed_start=round(start, 3),
                       sync_offset_ms=round((start - seg["start"]) * 1000), back_transcript=back, qa=q,
                       max_end=round(nxt_start - 0.03, 3))
            overrun = start + dur - rec["max_end"]
            if overrun > 0.05:
                notes.append(f"trimmed_overrun:{overrun:.2f}s")
            rec["status"] = "dubbed" if q["ok"] and plan.action != "condense" else "degraded"
            if plan.action == "condense":
                notes.append("still_too_long_after_condense")
            return rec, clip
        except (BodhanError, RuntimeError) as e:
            rec["error"] = str(e)[:300]
            rec["translation"] = translation or ""
            log("segment_failed", lang=self.lang, idx=i, error=rec["error"])
            return rec, None

    def custom_segment(self, rec: dict, seg: dict, nxt_start: float, ov: dict, src: Path, clip: Path, translation: str) -> dict:
        """The person's own recording replaces Indic-Speak for this line. It is placed and fitted like any dub
        (same ladder, speed never above the cap) but is never regenerated, so there is no QA retry loop."""
        raw = src.read_bytes()
        d = audio.wav_duration(raw)
        slot = seg["end"] - seg["start"]
        plan = timing.plan_fit(d, slot, seg["gap_after"], seg["gap_before"], min(float(ov.get("max_stretch", config.STRETCH_MAX)), config.STRETCH_MAX))
        if ov.get("speed"):
            sp = min(config.STRETCH_MAX, max(config.STRETCH_MIN, float(ov["speed"])))
            plan = timing.FitPlan("manual_speed", sp, 0.0, 0.0)
        dur = audio.fit_clip(raw, plan.stretch, clip)
        start = seg["start"] + plan.start_shift
        max_end = round(nxt_start - 0.03, 3)
        rec["voice"], rec["custom_voice"] = "Your voice", True
        rec["notes"].append("custom_voice")
        rec["attempts"].append({"cond": 0, "tts": 0, "raw_s": round(d, 2), "action": plan.action, "stretch": round(plan.stretch, 3),
                                "cer": 0.0, "flags": ["custom_voice"]})
        log("segment_custom_voice", lang=self.lang, idx=rec["idx"], raw_s=round(d, 2), action=plan.action)
        overrun = start + dur - max_end
        if overrun > 0.05:
            rec["notes"].append(f"trimmed_overrun:{overrun:.2f}s")
        rec.update(translation=translation, audio_duration=round(dur, 3), action=plan.action, stretch=round(plan.stretch, 3),
                   placed_start=round(start, 3), sync_offset_ms=round((start - seg["start"]) * 1000),
                   back_transcript="(your own recording; not checked by the speech model)",
                   qa={"ok": True, "cer": 0.0, "flags": ["custom_voice"]}, max_end=max_end,
                   status="degraded" if plan.action == "condense" else "dubbed")
        if plan.action == "condense":
            rec["notes"].append("recording_longer_than_slot_at_max_speed")
        return rec

    # ---- on-screen text
    def onscreen_overlays(self) -> tuple[list[dict], dict]:
        """Translate each detected on-screen text and render its overlay. Returns (overlays, summary)."""
        data = self.src.get("onscreen")
        if not data or not data.get("items"):
            return [], {"enabled": bool(data), "items": 0} if data is not None else {}
        W, H = onscreen.video_size(self.opt.input)
        font, font_note = onscreen.find_font(self.lang)
        odir = self.work / "onscreen"
        odir.mkdir(parents=True, exist_ok=True)
        rows, overlays = [], []
        for it in data["items"]:
            ov = self.overrides.get(f"o{it['id']}", {})
            row = {"id": it["id"], "start": it["start"], "end": it["end"], "bbox": it["bbox"], "original": it["text"],
                   "label": it.get("label", ""), "hidden": bool(ov.get("hide")), "translation": "", "status": "ok"}
            rows.append(row)
            if row["hidden"]:
                row["status"] = "hidden"
                continue
            try:
                text = ov.get("text") or self.translate(it["text"])[0]
                row["translation"] = text
                info = onscreen.render_overlay(it, text, font, W, H, odir / f"{it['id']}.png")
                overlays.append({**info, "start": it["start"], "end": it["end"]})
                row["font_size"] = info["font_size"]
                if info["overflow"]:
                    row["status"] = "tight"
            except Exception as e:          # one text failing must not remove the others
                row["status"] = "failed"
                row["error"] = str(e)[:160]
                log("onscreen_item_failed", lang=self.lang, id=it["id"], error=str(e)[:200])
        textutil.atomic_write(self.dir / f"onscreen.{self.lang}.json", json.dumps(
            {"lang": self.lang, "font": font, "font_note": font_note, "video": [W, H], "items": rows}, ensure_ascii=False, indent=1))
        n_ok = len(overlays)
        summ = {"enabled": True, "items": len(rows), "burned_in": n_ok, "hidden": sum(r["hidden"] for r in rows),
                "failed": sum(r["status"] == "failed" for r in rows), "font": font_note, "status": "ok"}
        if font_note in ("no_font", "fallback_may_lack_glyphs"):
            summ["warning"] = "no font for this script was found; install fonts-noto-core (Linux) or use Windows 10+ (Nirmala UI)"
        log("lang_stage", lang=self.lang, stage="onscreen", **{k: v for k, v in summ.items() if k != "warning"})
        return overlays, summ

    # ---- whole language
    def run(self) -> dict:
        t0 = time.time()
        log("lang_stage", lang=self.lang, stage="segments", total=len(self.src["segments"]))
        segs, dur = self.src["segments"], self.src["duration"]
        recs, clips = [], []
        with ThreadPoolExecutor(self.opt.concurrency) as ex:
            futs = [ex.submit(self.segment, s, segs[k + 1]["start"] if k + 1 < len(segs) else dur)
                    for k, s in enumerate(segs)]
            for f in futs:
                r, c = f.result()
                recs.append(r)
                if c:
                    clips.append({"path": c, "start": r["placed_start"], "max_end": r["max_end"]})
        log("lang_stage", lang=self.lang, stage="mix")
        mix_info = audio.render_mix(self.opt.input, dur, [(s["start"], s["end"]) for s in segs], clips,
                                    self.work / "mix.wav", self.work)
        out_video = self.dir / f"video.{self.lang}.mp4"
        overlays, osum = self.onscreen_overlays()
        try:
            if overlays:
                onscreen.mux(self.opt.input, self.work / "mix.wav", out_video, dur, overlays)
            else:
                audio.mux(self.opt.input, self.work / "mix.wav", out_video, dur)
        except RuntimeError as e:
            if not overlays:
                raise
            log("onscreen_mux_failed", lang=self.lang, error=str(e)[:300])
            osum.update(status="overlay_failed", error=str(e)[:200], burned_in=0)
            audio.mux(self.opt.input, self.work / "mix.wav", out_video, dur)        # the dub is never lost to an overlay problem
        self.onscreen_summary = osum
        srt = timing.make_srt([(r["placed_start"] if "placed_start" in r else r["start"], r["end"],
                                textutil.wrap_subtitle(r.get("translation", ""))) for r in recs if r.get("translation")])
        textutil.atomic_write(self.dir / f"subtitles.{self.lang}.srt", srt)
        out_dur = audio.probe_duration(out_video)
        log("lang_stage", lang=self.lang, stage="done")
        for r in recs:
            r.pop("attempts_full", None)
        textutil.atomic_write(self.dir / f"segments.{self.lang}.json",
            json.dumps({"lang": self.lang, "label": config.LANGUAGES.get(self.lang, self.lang), "ai_dubbed": True,
                        "segments": recs, "skipped": self.src["skipped"]}, ensure_ascii=False, indent=1))
        res = self.summarize(recs, mix_info, out_dur, time.time() - t0)
        if getattr(self, "onscreen_summary", None):
            res["onscreen"] = self.onscreen_summary
        return res

    def summarize(self, recs, mix_info, out_dur, secs) -> dict:
        dubbed = [r for r in recs if "placed_start" in r]
        stretches = [r["stretch"] for r in dubbed]
        offs = [abs(r["sync_offset_ms"]) for r in dubbed]
        cers = [r["qa"]["cer"] for r in dubbed]
        miss = []
        bad_sync = [r["idx"] for r in dubbed if abs(r["sync_offset_ms"]) > config.SYNC_TOL_S * 1000]
        failed = [r["idx"] for r in recs if r["status"] == "failed"]
        degraded = [r["idx"] for r in recs if r["status"] == "degraded"]
        if bad_sync:
            miss.append({"target": "segment_sync", "segments": bad_sync, "why": "start moved more than 300 ms"})
        if degraded:
            miss.append({"target": "speed_change_or_fidelity", "segments": degraded,
                         "why": "line still too long at 1.2x after condensing, or back-transcript flagged repeat/omission/mismatch"})
        if failed:
            miss.append({"target": "coverage", "segments": failed, "why": "API error after retries; see segments json"})
        len_diff = abs(out_dur - self.src["duration"]) * 1000
        if len_diff > config.LENGTH_TOL_S * 1000:
            miss.append({"target": "total_length", "why": f"output differs by {len_diff:.0f} ms"})
        lufs = mix_info.get("speech_lufs")
        if lufs is not None and abs(lufs - config.LUFS_TARGET) > 1:
            miss.append({"target": "loudness", "why": f"speech at {lufs:.1f} LUFS (peak limiter or very short speech)"})
        return {
            "status": "ok" if not failed else "partial", "seconds": round(secs, 1),
            "segments": {"total": len(recs), "dubbed": sum(r["status"] == "dubbed" for r in recs),
                         "degraded": len(degraded), "failed": len(failed), "skipped_non_speech": len(self.src["skipped"])},
            "retries": {"tts": sum(r["retries"]["tts"] for r in recs), "condense": sum(r["retries"]["condense"] for r in recs)},
            "qa": {"mean_cer": round(sum(cers) / len(cers), 4) if cers else None,
                   "max_cer": max(cers) if cers else None,
                   "flagged": [{"idx": r["idx"], "flags": r["qa"]["flags"]} for r in dubbed if r["qa"]["flags"]]},
            "targets": {
                "sync_within_300ms_pct": round(100 * sum(o <= 300 for o in offs) / len(offs), 1) if offs else None,
                "stretch_range": [min(stretches), max(stretches)] if stretches else None,
                "coverage": f"{len(dubbed)}/{len(recs)} dubbed; {len(self.src['skipped'])} non-speech skipped (logged)",
                "output_length_diff_ms": round(len_diff), "speech_lufs": lufs},
            "misses": miss,
        }


# ------------------------------------------------------------------ run
def add_costs(a: dict, b: dict) -> dict:
    """Sum two nested cost/meter dicts key by key."""
    out = dict(a)
    for k, y in b.items():
        x = out.get(k)
        if isinstance(y, dict):
            out[k] = add_costs(x if isinstance(x, dict) else {}, y)
        elif isinstance(y, (int, float)):
            out[k] = round((x if isinstance(x, (int, float)) else 0) + y, 4)
        else:
            out[k] = y
    return out


def preflight(client: BodhanClient, lang: str) -> None:
    """Cheap end-to-end probe of translate and speak (a few characters, cached) BEFORE spending on transcription,
    so a wrong key or endpoint fails in seconds with a clear message instead of after a long run."""
    t = client.translate("Hello.", lang)["text"] or "Hello."
    client.speak(t, config.DEFAULT_VOICES["male"], lang)


def missing_tools() -> list[str]:
    return [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]


def run_pipeline(opt: Options, client: BodhanClient, brain: Brain) -> int:
    t0 = time.time()
    opt.out.mkdir(parents=True, exist_ok=True)
    work = opt.out / "work"
    work.mkdir(exist_ok=True)
    report = {"input": str(opt.input), "langs": opt.langs, "ai_dubbed": True, "llm": {"model": brain.model, "enabled": brain.available},
              "languages": {}, "errors": []}
    voices = textutil.load_yaml(opt.voices)
    rc = 0
    gone = missing_tools()
    if gone:                                  # fail first, in plain words, before any API call is made
        msg = (f"{' and '.join(gone)} not found on PATH. Install ffmpeg (Windows: winget install Gyan.FFmpeg; macOS: brew install ffmpeg; "
               "Linux: apt-get install ffmpeg), then open a NEW terminal, or add the folder that holds ffmpeg.exe to PATH. "
               "Check with: ffmpeg -version and ffprobe -version")
        log("fatal", stage="tools", error=msg)
        report["errors"].append(msg)
        textutil.atomic_write(opt.out / "report.json", json.dumps(report, indent=1))
        return 2
    try:
        preflight(client, next((x for x in opt.langs if x != "en"), "hi"))
        log("stage", stage="preflight", ok=True)
    except Exception as e:
        log("fatal", stage="preflight", error=str(e)[:300])
        report["errors"].append(f"Bodhan check failed before starting (nothing was spent on transcription): {e}")
        textutil.atomic_write(opt.out / "report.json", json.dumps(report, indent=1))
        return 2
    try:
        src = analyse_source(opt, client, brain, work)
    except Exception as e:  # nothing can be dubbed without the English analysis
        log("fatal", stage="source", error=str(e)[:300])
        report["errors"].append(f"source analysis failed: {e}")
        textutil.atomic_write(opt.out / "report.json", json.dumps(report, indent=1))
        return 2
    src["glossary"] = textutil.merge_glossary(textutil.load_glossary(opt.glossary),
                                              [g for g in src["glossary"] if g.get("source") == "llm"])
    textutil.atomic_write(opt.out / "transcript.en.json", json.dumps(
        {"duration": src["duration"], "segments": [{k: s[k] for k in ("idx", "start", "end", "text", "speaker", "gender")} for s in src["segments"]],
         "skipped": src["skipped"], "glossary": src["glossary"]}, ensure_ascii=False, indent=1))
    report["source"] = {"duration": src["duration"], "segments": len(src["segments"]), "grouping": src["grouping"],
                        "speakers": src["speakers"], "glossary_terms": len(src["glossary"]), "warnings": src["warnings"],
                        "seconds": src["seconds"]}
    src["onscreen"] = None
    if opt.ocr:
        try:
            src["onscreen"] = onscreen.extract(opt.input, client, work, src["duration"], src["sha"], opt.ocr_interval,
                                               opt.ocr_max_frames, min(opt.concurrency, 2))
            report["onscreen"] = {k: src["onscreen"][k] for k in ("frames", "ocr_calls", "interval", "skipped", "warnings")}
            report["onscreen"]["items"] = len(src["onscreen"]["items"])
        except Exception as e:     # on-screen text is an extra: never stop the dub for it
            log("onscreen_failed", error=str(e)[:300])
            report["onscreen"] = {"enabled": True, "error": str(e)[:300], "items": 0}
            src["warnings"] = list(src.get("warnings") or []) + [f"on-screen text skipped: {str(e)[:200]}"]
            report["source"]["warnings"] = src["warnings"]
    meters = {}

    def do_lang(lang):
        m = meters[lang] = Meter()
        try:
            res = LangRun(opt, lang, client.with_meter(m), brain, src, voices).run()
        except Exception as e:  # one language must never stop the others
            log("language_failed", lang=lang, error=str(e)[:300])
            res = {"status": "failed", "error": str(e)[:300]}
        res["cost"] = m.snapshot()
        return lang, res

    with ThreadPoolExecutor(len(opt.langs)) as ex:
        for lang, res in ex.map(do_lang, opt.langs):
            report["languages"][lang] = res
            if res["status"] != "ok":
                rc = 1
    rp = opt.out / "report.json"
    if opt.merge and rp.exists():
        old = json.loads(rp.read_text())
        for lang, res in report["languages"].items():
            prev = (old.get("languages") or {}).get(lang)
            res["cost_this_run"] = res["cost"]
            if prev and "cost" in prev:
                res["cost"] = add_costs(prev["cost"], res["cost"])
        report["languages"] = {**(old.get("languages") or {}), **report["languages"]}
        report["langs"] = list(report["languages"])
    report["cost_inr"] = round(sum(r["cost"]["total_cost_inr"] for r in report["languages"].values()), 4)
    report["cost_note"] = "source transcription is counted in the first run only; cached calls cost nothing"
    report["source_cost"] = client.meter.snapshot()
    report["total_seconds"] = round(time.time() - t0, 1)
    report["llm"].update({"calls": brain.calls, "failed_calls": brain.failed_calls, "switched_off": brain.tripped, "last_error": brain.last_error})
    report["minutes_of_video"] = round(src["duration"] / 60, 2)
    textutil.atomic_write(opt.out / "report.json", json.dumps(report, ensure_ascii=False, indent=1))
    return rc
