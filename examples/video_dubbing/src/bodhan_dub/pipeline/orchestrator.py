"""Top-level orchestration.

Shared work (ingest → segment → transcribe → glossary → speakers) runs once; then
each language is processed in an isolated task so one language's failure never
stops the others. Partial outputs and the report are always written, and the
process exits non-zero if any language failed.
"""

from __future__ import annotations

import asyncio
import copy
import os

from ..cache import DiskCache, NullCache
from ..config import SUPPORTED_LANGS, Settings
from ..logging_setup import Timer, get_logger
from ..models.base import BodhanAPI, CostMeter, make_limiters
from ..models.llm import LLMBrain
from ..models.voices import VoiceRegistry
from ..types import LanguageResult
from . import qa as qa_mod
from . import report as report_mod
from .assemble import assemble_language
from .glossary import build_glossary
from .ingest import ingest
from .segment import segment_audio
from .speakers import assign_speakers, gender_for
from .subtitles import default_sub_font, write_srt, write_vtt
from .transcribe_stage import transcribe_segments
from .translate_stage import translate_segments
from .tts_stage import synth_and_fit

log = get_logger("orchestrator")


def _merge_cost(meters: list[CostMeter]) -> dict:
    total = CostMeter()
    for m in meters:
        total.transcribe_seconds += m.transcribe_seconds
        total.translate_tokens += m.translate_tokens
        total.speak_chars += m.speak_chars
        for k in total.calls:
            total.calls[k] += m.calls[k]
    out = total.inr
    out["calls"] = total.calls
    return out


async def _process_segment(api, brain, dub, lang, voice, glossary, settings, segdir, work_sr, cache):
    """Synthesize + fit one segment, then QA with a bounded misbehaviour retry."""
    lang_asr = SUPPORTED_LANGS.get(lang, {}).get("asr", lang)
    try:
        await synth_and_fit(api, brain, dub, lang, voice, settings, segdir, work_sr, cache,
                            glossary_hint=glossary.as_prompt_hint())
        await qa_mod.measure_segment(api, dub, lang_asr, settings)
        for style in qa_mod.STYLE_FALLBACKS:
            if not dub.qa.misbehavior or dub.retries >= 2:
                break
            dub.retries += 1
            dub.style = style  # recover with an alternate delivery style
            await synth_and_fit(api, brain, dub, lang, voice, settings, segdir, work_sr, cache,
                                glossary_hint=glossary.as_prompt_hint())
            await qa_mod.measure_segment(api, dub, lang_asr, settings)
    except Exception as e:  # one segment failing must not sink the language
        dub.status, dub.reason = "failed", str(e)
        log.warning("segment failed", extra={"lang": lang, "seg": dub.index,
                    "event": "seg_failed", "extra": {"error": str(e)}})


async def _process_language(settings, base_segments, non_speech, total, ingest_info,
                            brain, glossary, voices, sem, cache, limiters) -> tuple[LanguageResult, CostMeter]:
    lang = base_segments["lang"]
    segments = base_segments["segments"]
    r = LanguageResult(lang=lang)
    cost = CostMeter()
    timings: dict = {}
    try:
        langdir = os.path.join(settings.out, lang)
        segdir = os.path.join(langdir, "seg")
        os.makedirs(segdir, exist_ok=True)

        async with BodhanAPI(settings, sem, cost, limiters) as api:
            with Timer(timings, "translate"):
                dubs = await translate_segments(api, segments, lang,
                                                code_mixed=settings.code_mixed, cache=cache)
            with Timer(timings, "tts"):
                await asyncio.gather(*(
                    _process_segment(api, brain, d, lang,
                                     voices.pick(lang, gender_for(d.speaker)),
                                     glossary, settings, segdir,
                                     ingest_info["work_sr"], cache)
                    for d in dubs if d.status != "skipped"
                ))

        out_mp4 = os.path.join(langdir, f"video.{lang}.mp4")
        tmpdir = os.path.join(langdir, ".work")
        os.makedirs(tmpdir, exist_ok=True)

        # write subtitles first so they can be embedded as a soft track during mux
        srt = os.path.join(langdir, f"subtitles.{lang}.srt")
        write_srt(dubs, srt)
        write_vtt(dubs, os.path.join(langdir, f"subtitles.{lang}.vtt"))

        with Timer(timings, "assemble"):
            info = assemble_language(ingest_info["bed_wav"], non_speech, dubs,
                                     settings.input, out_mp4, total,
                                     ingest_info["work_sr"], settings.target_lufs, tmpdir,
                                     subtitles=srt, sub_lang=lang)
        timings["loudness"] = info["loudness_lufs"]

        report_mod.write_segments(dubs, os.path.join(langdir, f"segments.{lang}.json"))

        if settings.burn_subs:
            subbed = os.path.join(langdir, f"video.{lang}.subbed.mp4")
            try:
                from ..ffmpeg import burn_subtitles
                burn_subtitles(out_mp4, srt, subbed,
                               font=settings.sub_font or default_sub_font(lang))
                r.burned_video_path = subbed
            except Exception as e:  # best-effort; never fail the language over a font issue
                log.warning("burn-in failed", extra={"lang": lang, "event": "burn_failed",
                            "extra": {"error": str(e)}})

        r.segments = dubs
        r.video_path = out_mp4
        r.subtitles_path = srt
        r.timings = timings
        r.retries = sum(d.retries for d in dubs)
        failed = sum(1 for d in dubs if d.status == "failed")
        r.status = "partial" if failed else "ok"
    except Exception as e:
        r.status = "failed"
        r.error = str(e)
        r.timings = timings
        log.error("language failed", exc_info=True,
                  extra={"lang": lang, "event": "lang_failed", "extra": {"error": str(e)}})
    r.cost_inr = cost.inr["total"]
    return r, cost


async def run(settings: Settings) -> int:
    os.makedirs(settings.out, exist_ok=True)
    workdir = os.path.join(settings.out, ".work")
    sem = asyncio.Semaphore(settings.concurrency)
    limiters = make_limiters(settings.rate_interval)  # shared per-model pacing across all calls
    brain = LLMBrain(settings)
    voices = VoiceRegistry.load(settings.voices_path, mock=settings.mock)
    cache = DiskCache(settings.cache_dir) if settings.resume else NullCache()
    run_timings: dict = {}
    shared_cost = CostMeter()

    # ---- shared pre-steps (run once for all languages) ----
    async with BodhanAPI(settings, sem, shared_cost, limiters) as api:
        with Timer(run_timings, "ingest"):
            ingest_info = ingest(settings.input, workdir)
        with Timer(run_timings, "segment"):
            segments, non_speech, total = segment_audio(ingest_info["asr_wav"], settings.vad)
        with Timer(run_timings, "transcribe"):
            await transcribe_segments(api, ingest_info["asr_wav"], segments, cache)
        assign_speakers(segments, ingest_info["asr_wav"])
        transcript_text = " ".join(s.text for s in segments if s.text)
        with Timer(run_timings, "glossary"):
            glossary = await build_glossary(brain, transcript_text, settings.glossary_path)

    report_mod.write_transcript(segments, total,
                                os.path.join(settings.out, "transcript.en.json"))

    # ---- per-language, isolated ----
    tasks = [
        _process_language(settings,
                          {"lang": lang, "segments": copy.deepcopy(segments)},
                          non_speech, total, ingest_info, brain, glossary, voices, sem, cache, limiters)
        for lang in settings.langs
    ]
    outcomes = await asyncio.gather(*tasks)
    results = [r for r, _ in outcomes]
    cost = _merge_cost([shared_cost] + [c for _, c in outcomes])

    report = report_mod.build_report(settings, ingest_info, results, cost, run_timings,
                                     os.path.join(settings.out, "report.json"))
    any_failed = any(r.status == "failed" for r in results)
    log.info("run complete", extra={"event": "done",
             "extra": {"status": report["status"], "cost_inr": cost["total"],
                       "languages": {r.lang: r.status for r in results}}})
    return 1 if any_failed else 0
