"""End-to-end run: one video -> dubbed videos, subtitles and segment files for each language + report.json.

Stages (all ASR / translation / TTS calls are Bodhan models; the LLM only edits English and judges):
  1. audio      container-aligned 16 kHz mono of the source
  2. separate   gate: is there music/noise under the voice? if yes, split voice / background (Demucs, local)
  3. chunk+ASR  speech timing from the voice, Indic-Transcribe on the original audio (cut-off replies re-split)
  4. English    punctuation (LLM, words unchanged), pause-level alignment (pieces between pauses transcribed
                again, so each sentence break goes to the pause where the words really change), sentences on
                real pauses, boundary review, dubbing units
  5. per lang   Indic-Translate -> Indic-Speak -> fit (pauses, 0.85-1.2x, condense) -> QA -> place
                -> closed sync loop -> mix (background kept under the dub) -> mux -> verify
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from . import condense as condense_mod
from . import glossary as glossary_mod
from . import llm as llm_mod
from . import logs
from . import transcribe as transcribe_mod
from .align import align_chunks
from .api import exhausted_services
from .assemble import assemble
from .audio import extract_audio, probe_media
from .chunking import chunk_audio
from .dub_language import DubConfig, dub_language
from .punctuate import punctuate_chunks
from .review import review_boundaries
from .sentences import build_sentences, snap_stats
from .separate import extract_mix, load_model, probe_background, separate
from .sync_check import measure_sync, source_regions_from_chunks, verify_mux
from .translate import lang_name, translate_units
from .transcribe import transcribe_chunks
from .units import split_sentences
from .vad import detect_speech

log = logging.getLogger(__name__)

# Voice names: indic-speak model card; genders: the Cookbook's scripts/bodhan_api_rules.json. Defaults are the
# female voice of each language, so every language gets the same kind of voice. Measured pitch agrees for all
# 12 pairs (female 192-300 Hz, male 104-175 Hz; probe sentence, 2026-11-20). English uses Kavya, who is also in
# the card's English list.
DEFAULT_VOICES = {"hi": "Kavya", "ta": "Anitha", "bn": "Ishita", "mr": "Anagha", "te": "Sravani", "gu": "Dhara",
                  "kn": "Deepika", "ml": "Lakshmi", "or": "Itishree", "pa": "Kaur", "as": "Prastuti", "ur": "Saba",
                  "en": "Kavya"}
# The male voice of each language, for --voices or future per-speaker voices.
OTHER_VOICES = {"hi": "Amit", "ta": "Arun", "bn": "Sourav", "mr": "Chinmay", "te": "Vamsi", "gu": "Parth",
                "kn": "Adarsh", "ml": "Kiran", "or": "Akash", "pa": "Manpreet", "as": "Ankur", "ur": "Zaid"}
ISO639_2 = {"hi": "hin", "ta": "tam", "bn": "ben", "te": "tel", "mr": "mar", "gu": "guj", "kn": "kan",
            "ml": "mal", "pa": "pan", "or": "ori", "as": "asm", "ur": "urd", "en": "eng"}
PRICE = {"asr_per_min": 0.10, "translate_per_10k_tokens": 0.20, "tts_per_10k_chars": 6.0}


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def run(video: str | Path, langs: list[str], out: str | Path, voices: dict | None = None,
        style: str | None = "educational lecture", max_duration: float | None = None,
        separation: str = "auto", sync_rounds: int = 2, qa: bool = True, align: bool = True,
        lang_workers: int = 3, glossary: dict | str | Path | None = None, code_mixed: bool = False) -> dict:
    """Run the whole pipeline. Every log record of the run also goes to <out>/run.log.jsonl (logs.py).

    separation: 'auto' (gate decides), 'on' or 'off'. max_duration: dub only the first N seconds.
    align: pause-level alignment of sentence breaks (costs about one more ASR pass over multi-pause chunks).
    lang_workers: languages translated and dubbed at the same time (mixing and verifying take turns).
    glossary: {english term: {lang: rendering}} or a path to a glossary YAML (glossary.py); checked, retried
    once and reported per language. code_mixed: translate into "<Language> (code-mixed)"."""
    handler = logs.attach_run_log(out)
    try:
        return _run(video, langs, out, voices=voices, style=style, max_duration=max_duration,
                    separation=separation, sync_rounds=sync_rounds, qa=qa, align=align, lang_workers=lang_workers,
                    glossary=glossary, code_mixed=code_mixed)
    except Exception:
        log.exception("run failed")
        raise
    finally:
        logs.detach_run_log(handler)


def _run(video, langs, out, voices, style, max_duration, separation, sync_rounds, qa, align, lang_workers,
         glossary, code_mixed) -> dict:
    t_start = time.perf_counter()
    timings: dict[str, float] = {}
    llm_mod.reset_stats()
    condense_mod.reset_stats()
    transcribe_mod.reset_stats()

    @contextmanager
    def timer(name: str):
        t0 = time.perf_counter()
        yield
        timings[name] = round(timings.get(name, 0.0) + time.perf_counter() - t0, 1)

    video, out = Path(video), Path(out)
    work = out / "work"
    work.mkdir(parents=True, exist_ok=True)
    voices = {**DEFAULT_VOICES, **(voices or {})}
    if isinstance(glossary, (str, Path)):
        glossary = glossary_mod.load_glossary(glossary)   # a missing file stops here, before any paid call
    langs = list(dict.fromkeys(langs))      # "hi,hi" -> one run
    for lang in langs:                      # fail fast, before any paid call
        lang_name(lang)
        if lang not in voices:
            raise ValueError(f"no voice for '{lang}': pass --voices {lang}=<VoiceName>")
    log.info("run start: %s -> %s, languages %s", video.name, out, ",".join(langs),
             extra={"data": {"input": str(video), "langs": langs, "voices": {k: voices[k] for k in langs},
                             "style": style, "max_duration": max_duration, "separation": separation,
                             "sync_rounds": sync_rounds, "qa": qa, "align": align, "lang_workers": lang_workers,
                             "glossary_terms": len(glossary or {}), "code_mixed": code_mixed}})
    media = probe_media(video)
    if not media["has_audio"]:
        raise ValueError(f"{video} has no audio stream")
    total = min(max_duration, media["duration"]) if max_duration else media["duration"]
    trimmed = total < media["duration"] - 0.01

    # 1. audio --------------------------------------------------------------------------------------
    with timer("extract_audio"):
        audio16 = extract_audio(video, work / "audio_16k.wav", duration=total)

    # 2. separation gate ----------------------------------------------------------------------------
    sep_report: dict = {"mode": separation}
    sep = None
    with timer("separation"):
        if separation != "off":
            model = load_model()
            if separation == "auto":
                mix = extract_mix(video, work / "separate", total)
                probe = probe_background(mix, detect_speech(audio16), model)
                sep_report["probe"] = probe
                need = probe["needed"]
            else:
                need = True
            if need:
                sep = separate(video, work / "separate", total, model=model)
                sep_report["separation"] = {k: v for k, v in sep.items() if not k.endswith("_path")}
        sep_report["used"] = sep is not None
    timing_wav = Path(sep["vocals_16k_path"]) if sep else audio16

    # 3. chunk + ASR --------------------------------------------------------------------------------
    with timer("asr"):
        chunks = chunk_audio(timing_wav, work / "chunks", asr_wav=audio16 if sep else None)
        raw = transcribe_chunks(chunks, work)

    # 4. English text: punctuation, sentences, review, units ---------------------------------------
    with timer("english"):
        punct = punctuate_chunks(raw, work)
        align_stats = None
        if align:
            with timer("align"):
                punct, align_stats = align_chunks(punct, chunks, work)
        sents = build_sentences(punct)
        _write(work / "sentences.json", sents)
        reviewed, audit = review_boundaries(sents, work)
        _write(work / "sentences_reviewed.json", reviewed)
        _write(work / "review_audit.json", audit)
        units = split_sentences(reviewed)
        _write(work / "units.json", units)
    _write(out / "transcript.en.json", [{"id": u["id"], "start": u["start"], "end": u["end"], "text": u["text"],
                                         "timing": u.get("timing")} for u in units])
    source_regions = source_regions_from_chunks(raw)
    asr_billed = sum(r.get("billed_sec", 0.0) for r in raw) + (align_stats or {}).get("billed_sec", 0.0)

    # 5. languages ----------------------------------------------------------------------------------
    # Translate + dub run in parallel across languages: that part is mostly waiting on the APIs. Mixing and
    # verifying hold whole-video audio in memory, so only one language does them at a time.
    heavy = threading.Lock()
    t_langs = time.perf_counter()

    def one_language(lang: str) -> tuple[str, dict]:
        logs.LANG.set(lang)          # every record from this thread is tagged with the language
        t_lang = time.perf_counter()
        lang_dir = out / lang
        try:
            with timer(f"{lang}.translate"):
                segs = translate_units(units, lang, work, glossary=glossary or None, code_mixed=code_mixed)
            cfg = DubConfig(lang=lang, voice=voices[lang], style=style, qa=qa, sync_rounds=sync_rounds,
                            glossary=glossary or None, code_mixed=code_mixed)
            with timer(f"{lang}.dub"):
                recs, summary = dub_language(segs, total, work, cfg)
            mp4 = lang_dir / f"video.{lang}.mp4"
            with heavy:
                with timer(f"{lang}.assemble"):
                    asm = assemble(recs, segs, video, mp4, work / lang / "assemble",
                                   duration=total if trimmed else None,
                                   background=sep["background_path"] if sep else None,
                                   sub_lang=ISO639_2.get(lang, "und"), label=f"AI-dubbed ({lang_name(lang)})")
                with timer(f"{lang}.verify"):
                    sync = measure_sync(asm["speech_track"], recs, segs, work / lang / "sync", source_regions)
                    mux = verify_mux(mp4, asm["final_audio"], work / lang / "sync")
                    direct = None if sep else measure_sync(mp4, recs, segs, work / lang / "sync_mp4", source_regions)
            shutil.copyfile(work / lang / "assemble" / "subtitles.srt", lang_dir / f"subtitles.{lang}.srt")
            seg_out = [_segment_out(s, r, cfg) for s, r in _pair(segs, recs)]
            _write(lang_dir / f"segments.{lang}.json", seg_out)
            for so in seg_out:       # one final record per segment: the whole story of that line
                log.info("segment %s final: %s", so.get("id"), so.get("status"), extra={"seg": so.get("id"), "data": so})
            return lang, _lang_report(lang, cfg, segs, recs, summary, asm, sync, mux, direct,
                                      time.perf_counter() - t_lang)
        except Exception as e:  # one language failing never stops the others
            log.exception("language %s failed", lang)   # partial outputs already written stay in place
            return lang, {"status": "failed", "error": f"{type(e).__name__}: {e}",
                          "seconds": round(time.perf_counter() - t_lang, 1)}

    with ThreadPoolExecutor(max_workers=max(1, min(lang_workers, len(langs)))) as ex:
        done = dict(ex.map(one_language, langs))
    lang_reports = {lang: done[lang] for lang in langs}
    timings["languages_wall"] = round(time.perf_counter() - t_langs, 1)

    seconds = round(time.perf_counter() - t_start, 1)
    report = {
        "input": str(video), "duration_s": round(media["duration"], 3), "dubbed_s": round(total, 3),
        "video_codec": media.get("video_codec"), "log": logs.LOG_NAME,
        "trimmed": trimmed, "languages": langs, "seconds_total": seconds, "timings_s": timings,
        "separation": sep_report,
        "code_mixed": code_mixed,
        "glossary_terms": len(glossary) if glossary else 0,
        "english": {"chunks": len(raw), "asr_failed": sum(not r["ok"] for r in raw),
                    "asr_resplit_chunks": sum(1 for r in raw if r.get("asr_pieces")),
                    "sentences": len(sents), "units": len(units), "boundary_snaps": snap_stats(punct),
                    "alignment": align_stats if align else "off",
                    "review_merges_applied": sum(1 for a in audit if a.get("applied"))},
        "cost_inr": _cost(asr_billed, lang_reports),
        "out_of_credit": exhausted_services(),
        "llm": {**llm_mod.STATS, "paced_s": round(llm_mod.STATS["paced_s"], 1),
                "unavailable": llm_mod.unavailable_reason()},
        "condense": dict(condense_mod.STATS),
        "asr_junk": dict(transcribe_mod.STATS),
        "per_language": lang_reports,
    }
    report["quality_targets"] = _targets(report)
    _write(out / "report.json", report)
    log.info("done in %.0fs -> %s", seconds, out / "report.json",
             extra={"data": {"status": {k: v.get("status") for k, v in lang_reports.items()},
                             "cost_inr": report["cost_inr"].get("total")}})
    return report


# --------------------------------------------------------------------------- report helpers
def _pair(segs: list[dict], recs: list[dict]):
    by_id = {r["id"]: r for r in recs}
    return [(s, by_id.get(s["id"])) for s in segs]


def _glossary_missing(r: dict, cfg) -> list[str]:
    """Glossary terms the final English of a dubbed unit contains but its final translation lacks."""
    if not cfg or not cfg.glossary or r.get("status") not in ("ok", "qa_failed"):
        return []
    return glossary_mod.missing_terms(r.get("en") or "", r.get("text") or "", cfg.glossary, cfg.lang)


def _r3(x):
    return None if x is None else round(float(x), 3)


def _qa_out(r: dict, cfg) -> dict | None:
    """The back-transcription QA result of the clip that was used."""
    if cfg is not None and not cfg.qa:
        return {"result": "off"}
    q = r.get("qa")
    if r.get("status") not in ("ok", "qa_failed"):
        return None
    if not q:
        return {"result": "unknown", "why": "back-transcription failed or came back as junk", "attempts":
                r.get("qa_attempts", 0)}
    return {"result": "pass" if q.get("ok") else "fail", "cer": q.get("cer"), "flags": q.get("flags", []),
            "insert_ratio": q.get("ins_ratio"), "delete_ratio": q.get("del_ratio"),
            "back_transcript": q.get("hyp"), "attempts": r.get("qa_attempts", 0)}


def _segment_out(s: dict, r: dict | None, cfg=None) -> dict:
    """One line of segments.<lang>.json: source text, translation, audio duration, QA result, timing."""
    r = r or {}
    keep = ("status", "text", "en", "voice", "speed", "pause_change", "onset", "voiced_end_at", "delay",
            "measured_start_err", "measured_end_err", "flags", "condense_rounds", "qa_attempts", "notes")
    out = {"id": s["id"], "start": s["start"], "end": s["end"], "source_text": s["src"],
           "translation_status": s["status"], **{k: r.get(k) for k in keep}}
    on, off = r.get("onset"), r.get("voiced_end_at")
    out["source_duration_s"] = _r3(s["end"] - s["start"])
    out["audio_duration_s"] = _r3(r.get("clip_dur"))        # the dubbed clip as placed (after pauses/speed)
    out["speech_duration_s"] = _r3(off - on) if on is not None and off is not None else None
    out["tts_duration_s"] = _r3(r.get("tts_voiced"))       # voiced length of the chosen TTS take, before fitting
    out["qa"] = _qa_out(r, cfg)
    out["qa_cer"] = (r.get("qa") or {}).get("cer")
    if cfg is not None and cfg.glossary:
        out["glossary_missing"] = _glossary_missing(r, cfg)
        out["glossary_retry"] = s.get("glossary_retry")
    return out


def _glossary_report(segs: list[dict], recs: list[dict], cfg) -> dict | None:
    """Per language: glossary term uses in the dubbed units, how many the final translation keeps, and the
    one-retry outcomes."""
    if not cfg.glossary:
        return None
    rend = glossary_mod.renderings_for(cfg.glossary, cfg.lang)
    uses = missing = 0
    miss_list = []
    for r in recs:
        if r.get("status") not in ("ok", "qa_failed"):
            continue
        terms = [t for t in rend if glossary_mod.match_en(t, r.get("en") or "")]
        lost = _glossary_missing(r, cfg)
        uses += len(terms)
        missing += len(lost)
        miss_list += [{"id": r["id"], "term": t, "wanted": rend[t]} for t in lost]
    return {"terms_for_language": len(rend), "term_uses": uses, "kept": uses - missing, "missing": missing,
            "missing_list": miss_list[:50],
            "retry": {o: sum(sg.get("glossary_retry") == o for sg in segs)
                      for o in ("fixed", "partly fixed", "not fixed", "no retry")}}


def _lang_report(lang, cfg, segs, recs, summary, asm, sync, mux, direct, seconds) -> dict:
    pu = sync["per_unit"]
    flagged = [{"id": r["id"], "status": r["status"], "flags": r.get("flags", []), "notes": r.get("notes", [])[-2:]}
               for r in recs if r.get("flags") or r["status"] != "ok"]
    tokens = sum((s.get("tokens") or 0) for s in segs if not s.get("cached"))
    return {
        "status": "ok" if summary["failed"] == 0 else "partial", "voice": cfg.voice, "seconds": round(seconds, 1),
        "code_mixed": cfg.code_mixed, "glossary": _glossary_report(segs, recs, cfg),
        "segments": summary["segments"], "dubbed": summary["dubbed"], "failed": summary["failed"],
        "qa_failed": summary["qa_failed"], "condensed": summary["condensed"],
        "translation": {k: sum(s["status"] == k for s in segs) for k in ("ok", "flagged", "failed")},
        "retries": {"tts_calls": summary["tts_calls"], "qa_attempts": sum(r.get("qa_attempts", 0) for r in recs),
                    "condense_rounds": sum(r.get("condense_rounds", 0) for r in recs)},
        "qa_mean_cer": summary["mean_cer"],
        "speed": {"min": summary["speed_min"], "max": summary["speed_max"], "sped_up": summary["sped_up"],
                  "slowed_down": summary["slowed_down"], "pauses_adjusted": summary["pauses_adjusted"]},
        "sync": {"measured_on": "dubbed speech track (same detector as the source voice)",
                 **{k: pu[k] for k in ("units", "starts_within_0.3s", "starts_within_0.1s", "ends_within_0.5s",
                                       "start_err_abs_mean", "start_err_max", "end_err_mean")},
                 **sync.get("unit_free", {}), "closed_loop": summary["sync_loop"]},
        "sync_on_final_mp4": None if direct is None else {k: direct["per_unit"][k] for k in
                                                         ("starts_within_0.3s", "start_err_abs_mean", "end_err_mean")},
        "mux": mux, "video": asm.get("video"), "label": asm.get("label"),
        "length_error_ms": asm["length_error_ms"], "av_offset_error_ms": asm["av_offset_error_ms"],
        "speech_lufs": asm["speech_lufs_after"], "final_mix_lufs": asm["final_mix_lufs"],
        "background_under_dub": asm["background_under_dub"],
        "flags": {k: summary[k] for k in ("delayed", "sync_miss", "late_end", "early_end", "overrun")},
        "flagged_segments": flagged,
        "billing": {"tts_chars": summary["tts_chars_billed"], "translate_tokens": tokens,
                    "qa_asr_sec": round(sum(t.get("dur") or 0 for r in recs for t in r.get("tts_log", [])
                                            if t.get("ok") and not t.get("cached")) if cfg.qa else 0.0, 1)},
    }


def _cost(asr_sec: float, langs: dict) -> dict:
    tts = sum(l.get("billing", {}).get("tts_chars", 0) for l in langs.values())
    tok = sum(l.get("billing", {}).get("translate_tokens", 0) for l in langs.values())
    qa = sum(l.get("billing", {}).get("qa_asr_sec", 0) for l in langs.values())
    c = {"asr": round(asr_sec / 60 * PRICE["asr_per_min"], 2),
         "qa_back_transcription": round(qa / 60 * PRICE["asr_per_min"], 2),
         "translate": round(tok / 10000 * PRICE["translate_per_10k_tokens"], 3),
         "tts": round(tts / 10000 * PRICE["tts_per_10k_chars"], 2)}
    c["total"] = round(sum(c.values()), 2)
    c["note"] = "billed this run only (cached calls are free); QA cost estimated from clip lengths"
    return c


def _targets(rep: dict) -> dict:
    """The assignment's quality targets, per language: met or not, with the measured value."""
    out = {}
    for lang, l in rep["per_language"].items():
        if l.get("status") == "failed":
            out[lang] = {"failed": l.get("error")}
            continue
        s = l["sync"]
        out[lang] = {
            "start_within_300ms": {"value": f"{s['starts_within_0.3s']}/{s['units']}",
                                   "met": s["starts_within_0.3s"] == s["units"]},
            "speed_0.85_to_1.2": {"value": [l["speed"]["min"], l["speed"]["max"]],
                                  "met": l["speed"]["min"] is None or (l["speed"]["min"] >= 0.85 - 1e-6
                                                                       and l["speed"]["max"] <= 1.2 + 1e-6)},
            "back_transcription_cer": {"value": l["qa_mean_cer"], "reported": True},
            "length_within_100ms": {"value": l["length_error_ms"], "met": l["length_error_ms"] <= 100},
            "speech_minus16_lufs_pm1": {"value": l["speech_lufs"], "met": abs(l["speech_lufs"] + 16) <= 1},
        }
    mins = rep["dubbed_s"] / 60
    out["runtime"] = {"seconds": rep["seconds_total"], "video_minutes": round(mins, 2),
                      "languages": len(rep["languages"]),
                      "note": "target: 5-min video, 3 languages in < 30 min"}
    return out
