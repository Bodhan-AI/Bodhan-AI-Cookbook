"""Stage orchestration.

Shared, once per video:  ingest → VAD → chunk → transcribe → repair (LLM) → glossary (LLM) → voices/style
Per language, in parallel: translate + check + recover → speak + QA + fit → place + mix + mux → subtitles
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import chunking, glossary, media, mix, report, vad, voices
from .bodhan import BodhanClient
from .cache import DiskCache
from .config import Settings
from .llm import LLM
from .repair import RepairParams, Unit, repair_transcript
from .synth import DubLine, Synthesizer, choose_style
from .transcribe import transcribe_chunks
from .translate import LanguageResult, Translator

log = logging.getLogger("bodhan_dub.pipeline")

EXPECTED_MIN_S, EXPECTED_MAX_S = 60, 15 * 60


class PipelineError(RuntimeError):
    """A problem with the input or setup that stops the whole run (exit code 2)."""


@dataclass(frozen=True)
class RunOptions:
    input: Path
    langs: list[str]
    out: Path
    cache_dir: Path
    max_parallel: int = 4
    glossary: Path | None = None
    voices: Path | None = None
    code_mixed: bool = False


@dataclass
class RunResult:
    units: list[Unit]
    languages: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def failed(self) -> list[str]:
        return [lang for lang, r in self.languages.items() if r["status"] == "failed"]


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # allow_nan=False: a NaN or infinity must fail loudly here, not produce invalid JSON for readers.
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def run(opts: RunOptions, settings: Settings, client: BodhanClient | None = None,
        llm: LLM | None = None) -> RunResult:
    started = time.perf_counter()
    try:
        user_terms = glossary.load_user_glossary(opts.glossary) if opts.glossary else []
        voice_cfg = voices.load_voice_config(opts.voices) if opts.voices else voices.VoiceConfig()
    except (glossary.GlossaryError, voices.VoiceConfigError) as exc:
        raise PipelineError(str(exc)) from exc
    stage_s: dict[str, float] = {}
    media.require_ffmpeg()
    info = media.probe(opts.input)
    if not info.has_audio:
        raise PipelineError(f"input has no audio track: {opts.input}")
    if not EXPECTED_MIN_S <= info.duration_s <= EXPECTED_MAX_S:
        # Outside the supported 1–15 min range we still try: nothing here depends on length.
        log.warning("input length outside the supported 1-15 min range",
                    extra={"stage": "ingest", "duration_s": round(info.duration_s, 3)})
    log.info("input probed", extra={"stage": "ingest", "duration_s": round(info.duration_s, 3),
                                    "has_video": info.has_video, "langs": opts.langs})

    wav = media.extract_audio(opts.input, opts.out / "work" / "source.16k.wav")
    samples = vad.load_wav(wav)

    vad_params, chunk_params = vad.VadParams(), chunking.ChunkParams()
    speech = vad.detect_speech(samples, vad_params)
    chunks = chunking.pack_chunks(speech.regions, chunk_params,
                                  find_cut=lambda lo, hi: vad.quietest_point(speech.probs, lo, hi))
    log.info("speech segmented", extra={
        "stage": "vad", "backend": speech.backend, "regions": len(speech.regions), "chunks": len(chunks),
        "speech_s": round(sum(r.duration for r in speech.regions), 1),
        "forced_cuts": sum(c.forced_cut for c in chunks),
    })

    cache = DiskCache(opts.cache_dir)
    client = client or BodhanClient(settings, max_parallel=opts.max_parallel, cache=cache)
    llm = llm or LLM(settings, cache=cache)
    t0 = time.perf_counter()
    segments = transcribe_chunks(client, samples, chunks, max_workers=opts.max_parallel)
    stage_s["transcribe"] = time.perf_counter() - t0
    asr_failures = [seg for seg in segments if (seg.skipped or "").startswith("transcription failed")]
    if segments and asr_failures and all(seg.skipped for seg in segments):
        # Speech was found but none of it could be transcribed (bad key, no credit, outage):
        # every language would be an undubbed copy, so the run fails instead of reporting ok.
        error = f"transcription failed for all {len(segments)} speech chunks: {asr_failures[0].skipped}"
        log.error("run failed", extra={"stage": "transcribe", "error": error})
        result = RunResult(units=[], languages={lang: {"status": "failed", "error": error} for lang in opts.langs})
        write_json(opts.out / "report.json", {
            "status": "failed", "failed_languages": result.failed, "input": opts.input.name, "error": error,
            "runtime_s": round(time.perf_counter() - started, 1), "languages": result.languages,
            "usage": {"bodhan": client.usage.snapshot(), "llm": dict(llm.usage)},
        })
        return result
    t0 = time.perf_counter()

    repair_params = RepairParams()
    units, repair_report = repair_transcript(llm, segments, repair_params)
    terms = glossary.build_glossary(llm, units, user_terms)
    stage_s["repair_and_glossary"] = time.perf_counter() - t0

    write_json(opts.out / "transcript.en.json", {
        "source": opts.input.name,
        "duration_s": round(info.duration_s, 3),
        "vad": {"backend": speech.backend, **asdict(vad_params)},
        "chunking": asdict(chunk_params),
        "repair": {**asdict(repair_params), **asdict(repair_report)},
        "segments": [u.to_dict() for u in units],
        "raw_segments": [s.to_dict() for s in segments],
    })
    write_json(opts.out / "glossary.en.json", {"terms": [t.to_dict() for t in terms]})
    log.info("english transcript ready", extra={
        "stage": "repair", "raw_segments": len(segments), "units": len(units),
        "dropped": sum(u.dropped is not None for u in units),
        "fallback_batches": repair_report.fell_back, "glossary_terms": len(terms),
    })

    # One voice per speaker: pitch decides male/female per unit (with hysteresis).
    genders = voices.assign_genders([voices.median_pitch(samples, u.start, u.end) for u in units])
    style = voice_cfg.style or choose_style(llm, units)
    log.info("voices planned", extra={"stage": "voices", "style": style,
                                      "male_units": genders.count("male"), "female_units": genders.count("female")})

    translator = Translator(client, llm, terms, code_mixed=opts.code_mixed, max_workers=opts.max_parallel)
    synthesizer = Synthesizer(client, llm, translator, style=style, max_workers=opts.max_parallel)
    job = LanguageJob(opts=opts, units=units, genders=genders, voice_cfg=voice_cfg, info=info,
                      translator=translator, synthesizer=synthesizer)
    result = RunResult(units=units)
    # Languages run side by side; the client's semaphore still caps total API concurrency.
    with ThreadPoolExecutor(max_workers=len(opts.langs)) as pool:
        futures = {lang: pool.submit(run_language, job, lang) for lang in opts.langs}
    for lang, future in futures.items():
        result.languages[lang] = future.result()

    usage = client.usage.snapshot()
    runtime = report.runtime_check(time.perf_counter() - started, info.duration_s, len(opts.langs),
                                   usage["rate_limit_wait_s"])
    write_json(opts.out / "report.json", {
        "status": "partial" if result.failed else "ok",
        "failed_languages": result.failed,
        "input": opts.input.name,
        "duration_s": round(info.duration_s, 3),
        "runtime_s": round(time.perf_counter() - started, 1),
        "runtime_check": runtime,
        "shared_stage_timings_s": {k: round(v, 1) for k, v in stage_s.items()},
        "targets": report.TARGETS,
        "languages": result.languages,
        "transcript": {"raw_segments": len(segments), "units": len(units),
                       "skipped_raw": [{"id": s.id, "reason": s.skipped} for s in segments if s.skipped],
                       "dropped_units": [{"id": u.id, "reason": u.dropped} for u in units if u.dropped],
                       "repair_fallback_batches": repair_report.fell_back, "vad_backend": speech.backend},
        "models": {"asr": "indic-transcribe", "mt": "indic-translate", "tts": "indic-speak", "llm": llm.model,
                   "tts_style": style, "code_mixed": opts.code_mixed},
        "cost_inr": {"bodhan": usage["total_cost_inr"], "by_model": usage["cost_inr"],
                     "note": "LLM cost depends on the provider; token counts are in usage.llm"},
        "usage": {"bodhan": usage, "llm": dict(llm.usage)},
    })
    log.info("run finished", extra={"stage": "report", "failed_languages": result.failed,
                                    "runtime_s": round(time.perf_counter() - started, 1),
                                    "cost_inr": usage["total_cost_inr"]})
    return result


@dataclass
class LanguageJob:
    opts: RunOptions
    units: list[Unit]
    genders: list[str]
    voice_cfg: voices.VoiceConfig
    info: media.MediaInfo
    translator: Translator
    synthesizer: Synthesizer


def run_language(job: LanguageJob, lang: str) -> dict[str, Any]:
    """One language end to end. Any exception is contained here so the other languages finish,
    and whatever was produced before the failure is still written."""
    out_dir = job.opts.out / lang
    timings: dict[str, float] = {}
    translated = LanguageResult(lang=lang)
    lines: list[DubLine] = []
    mixed: mix.MixResult | None = None
    stage = "translate"
    try:
        t0 = time.perf_counter()
        translated = job.translator.translate_language(job.units, lang)
        timings["translate"] = time.perf_counter() - t0

        stage, t0 = "synthesize", time.perf_counter()
        voice_of = {g: voices.voice_for(lang, g, job.voice_cfg) for g in ("male", "female")}
        lines = job.synthesizer.synthesize_language(translated.units, job.units, job.genders, voice_of, lang,
                                                    job.info.duration_s)
        timings["synthesize"] = time.perf_counter() - t0

        # Per-unit errors become skips so one bad line can't sink a language, but a language
        # where every line failed (bad key, outage) must not report success, nor ship an
        # "AI-dubbed" video that only holds the original audio.
        wanted = [u for u in job.units if not u.dropped]
        if wanted and not any(ln.sentences for ln in lines):
            reasons = sorted({n for ln in lines for n in ln.notes})[:3]
            raise RuntimeError(f"none of {len(wanted)} units could be dubbed: {'; '.join(reasons)}")

        stage, t0 = "mix", time.perf_counter()
        mix.place(lines, job.info.duration_s)
        mixed = mix.render(lines, job.opts.input, job.info.duration_s, out_dir / f"video.{lang}.mp4",
                           job.opts.out / "work" / lang, lang, job.info.has_video)
        mix.write_srt(lines, out_dir / f"subtitles.{lang}.srt", job.info.duration_s)
        timings["mix"] = time.perf_counter() - t0
    except Exception as exc:  # noqa: BLE001 - isolation boundary: record, never propagate
        log.exception("language failed", extra={"stage": stage, "lang": lang})
        translated.status, translated.error = "failed", f"{stage}: {type(exc).__name__}: {exc}"
    summary = report.language_report(lang, translated, lines, input_s=job.info.duration_s,
                                     output_s=mixed.output_duration_s if mixed else None,
                                     speech_lufs=mixed.speech_lufs if mixed else None, timings=timings)
    write_json(out_dir / f"segments.{lang}.json", {
        "lang": lang, "summary": summary,
        "segments": [ln.to_dict() for ln in lines] if lines else [u.to_dict() for u in translated.units],
    })
    log.info("language finished", extra={"stage": "report", "lang": lang, "status": summary["status"],
                                         "misses": summary["misses"]})
    return summary
