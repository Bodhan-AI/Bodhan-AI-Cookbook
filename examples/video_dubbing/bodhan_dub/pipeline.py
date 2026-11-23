"""Pipeline orchestration and exit codes.

Exit codes are part of the contract:

* ``0`` — every requested stage finished;
* ``2`` — the request is unusable (bad flag, missing key);
* ``3`` — the run started but could not finish (a stage is missing, a language
  failed, the transcript has gaps). Partial artifacts and the report are still
  written;
* ``1`` — unexpected failure.

A stage implies its prerequisites, so ``--stage speak`` runs transcription,
translation and speech. A failure in one language never stops the others: each
language is wrapped separately and its status is recorded.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import asr, bed, languages, mix, mux, subtitles
from . import speak as speak_stage
from . import translate as translate_stage
from .cache import Cache
from .client import BodhanClient
from .config import Settings
from .cost import CostMeter
from .errors import BodhanDubError, ConfigError, StageNotImplemented
from .glossary import Glossary
from .llm import LLMClient
from .log import event
from .report import RunReport
from .voices import VoiceMap

STAGES = ("asr", "translate", "speak", "assemble")

SEGMENTS_SCHEMA = "bodhan-dub/segments/v1"


@dataclass
class RunArgs:
    input: Path
    langs: list[str]
    out: Path
    stage: str = "all"
    glossary: Path | None = None
    voices: Path | None = None
    code_mixed: bool = False
    force: bool = False
    burn_subtitles: bool = False


def _active_stages(stage: str) -> list[str]:
    if stage == "all":
        return list(STAGES)
    return list(STAGES[: STAGES.index(stage) + 1])


def run(args: RunArgs, settings: Settings, logger: Any) -> int:
    args.out.mkdir(parents=True, exist_ok=True)
    report = RunReport(
        input_path=args.input, out_dir=args.out, langs=args.langs, stage=args.stage
    )
    report.set_settings(settings.to_public_dict())
    if args.glossary and not args.glossary.exists():
        report.warn(f"glossary {args.glossary} does not exist; ignoring it")
    if args.voices and not args.voices.exists():
        report.warn(f"voices file {args.voices} does not exist; using language defaults")
    if args.code_mixed:
        report.warn(
            "--code-mixed is on: Indic-Translate runs with script=codemix, "
            "so English technical terms stay in Latin script."
        )

    glossary = Glossary.load(args.glossary)
    voices = VoiceMap.load(args.voices)
    for warning in voices.warnings:
        report.warn(warning)

    cache = Cache(settings.cache_dir, enabled=settings.cache_enabled, force_refresh=args.force)
    cost = CostMeter()
    client = BodhanClient(settings, logger, cache, cost)

    active = _active_stages(args.stage)
    started = time.monotonic()
    exit_code = 0
    asr_partial = False
    transcript: dict[str, Any] | None = None
    failed_languages: set[str] = set()
    current_stage: str | None = None

    try:
        if "asr" in active:
            current_stage = "asr"
            report.stage("asr", "running")
            transcript = asr.run(
                input_path=args.input,
                out_dir=args.out,
                settings=settings,
                client=client,
                cost=cost,
                logger=logger,
            )
            report.set_transcript(transcript)
            stats = transcript["stats"]
            current_stage = None
            if stats.get("interrupted"):
                report.stage(
                    "asr",
                    "interrupted",
                    segments=stats["segments"],
                    chunks=stats["chunks"],
                    failures=stats["failures"],
                )
                report.error("run interrupted; the partial transcript was kept")
                exit_code = 130
            elif stats.get("failures"):
                # A few missing chunks must not throw away a whole dub. Continue
                # with what we have and fail the run at the end, so the artefacts
                # are still produced and the gaps are named in the report.
                asr_partial = True
                report.stage(
                    "asr",
                    "partial",
                    segments=stats["segments"],
                    chunks=stats["chunks"],
                    failures=stats["failures"],
                )
                report.warn(
                    f"{stats['failures']} of {stats['chunks']} chunks failed; "
                    "the transcript has gaps. Rerun to retry only the missing audio."
                )
            else:
                report.stage("asr", "ok", segments=stats["segments"], chunks=stats["chunks"], failures=0)

        if exit_code == 0 and "translate" in active and transcript is not None:
            current_stage = "translate"
            failed_languages |= _run_translate(
                args,
                transcript,
                glossary=glossary,
                voices=voices,
                settings=settings,
                client=client,
                logger=logger,
                report=report,
            )
            current_stage = None
            if failed_languages:
                exit_code = 3

        if exit_code == 0 and "speak" in active and transcript is not None:
            current_stage = "speak"
            failed_languages |= _run_speak(
                args,
                transcript,
                voices=voices,
                glossary=glossary,
                settings=settings,
                client=client,
                logger=logger,
                cost=cost,
                report=report,
            )
            current_stage = None
            if failed_languages:
                exit_code = 3

        if exit_code == 0 and "assemble" in active and transcript is not None:
            current_stage = "assemble"
            assemble_failed = _run_assemble(
                args,
                transcript,
                settings=settings,
                logger=logger,
                report=report,
            )
            report.stage(
                "assemble",
                "ok" if not assemble_failed else "partial",
                languages=len(args.langs),
                failed=len(assemble_failed),
            )
            failed_languages |= assemble_failed
            current_stage = None
            if failed_languages:
                exit_code = 3

        if exit_code == 0 and asr_partial:
            exit_code = 3

    except StageNotImplemented as exc:
        # Not a crash: the earlier stages did real work that is worth keeping.
        report.stage(exc.stage, "not_implemented")
        report.error(str(exc))
        current_stage = None
        exit_code = 3
    except ConfigError as exc:
        _mark_failed(report, current_stage, exc)
        report.error(str(exc))
        exit_code = 2
    except BodhanDubError as exc:
        _mark_failed(report, current_stage, exc)
        report.error(str(exc))
        exit_code = 3
    except KeyboardInterrupt:
        report.error("interrupted by the user")
        exit_code = 130
    except Exception as exc:  # pragma: no cover - defensive backstop
        _mark_failed(report, current_stage, exc)
        report.error(f"unexpected failure: {type(exc).__name__}: {exc}")
        event(logger, "pipeline.unexpected", level=40, stage="pipeline", error=repr(exc))
        exit_code = 1
    finally:
        elapsed = time.monotonic() - started
        report.data["run"] = {
            "elapsed_s": round(elapsed, 3),
            "exit_code": exit_code,
            "status": "ok" if exit_code == 0 else "failed",
        }
        report.set_cost(cost.summary())
        report.set_cache(cache.stats())
        report.set_ratelimit(client.limiter_stats())
        report.set_rate_limits(client.rate_limits())
        targets = report.data["quality_targets"]
        targets["runtime"]["observed"] = f"{elapsed:.0f}s for {len(args.langs)} language(s)"
        targets["runtime"]["status"] = "pass" if elapsed <= 1800 else "partial"
        path = report.write()
        event(
            logger,
            "run.finished",
            stage="pipeline",
            exit_code=exit_code,
            elapsed_s=round(elapsed, 2),
            cost_inr=cost.total_inr,
            report=str(path),
        )

    if exit_code == 0:
        _print_summary(args, report)
    return exit_code


# -- language stages ------------------------------------------------------


def _run_translate(
    args: RunArgs,
    transcript: dict[str, Any],
    *,
    glossary: Glossary,
    voices: VoiceMap,
    settings: Settings,
    client: BodhanClient,
    logger: Any,
    report: RunReport,
) -> set[str]:
    failed: set[str] = set()
    for lang in args.langs:
        try:
            result = translate_stage.translate_segments(
                transcript["segments"],
                lang=lang,
                client=client,
                glossary=glossary,
                code_mixed=args.code_mixed,
                max_block_chars=settings.block_chars,
                logger=logger,
            )
            voice, voice_warnings = voices.resolve(lang)
            for warning in voice_warnings:
                report.warn(warning)
            payload = {
                "language": lang,
                "language_name": languages.name(lang),
                "voice": voice,
                "style": settings.tts_style,
                "code_mixed": args.code_mixed,
                "source_segments": len(transcript["segments"]),
                "blocks": result["blocks"],
                "glossary_issues": result["glossary_issues"],
                "segments": result["segments"],
                "stats": {
                    "segments": len(result["segments"]),
                    "blocks": len(result["blocks"]),
                    "empty_slots": sum(1 for item in result["segments"] if item.get("empty")),
                    "glossary_issues": len(result["glossary_issues"]),
                    "alignment": result["alignment"],
                    "truncated_blocks": result["truncated_blocks"],
                },
            }
            _write_segments(args.out, lang, payload)
            report.set_language(
                lang,
                "translated",
                segments=len(result["segments"]),
                blocks=len(result["blocks"]),
                empty_slots=payload["stats"]["empty_slots"],
                glossary_issues=len(result["glossary_issues"]),
            )
            event(
                logger,
                "translate.done",
                stage="translate",
                language=lang,
                segments=len(result["segments"]),
                blocks=len(result["blocks"]),
                empty_slots=payload["stats"]["empty_slots"],
                glossary_issues=len(result["glossary_issues"]),
            )
        except Exception as exc:  # one language must not stop the others
            failed.add(lang)
            report.set_language(lang, "translate_failed", error=f"{type(exc).__name__}: {exc}"[:300])
            event(
                logger,
                "translate.failed",
                level=40,
                stage="translate",
                language=lang,
                error=str(exc)[:200],
            )
    return failed


def _run_speak(
    args: RunArgs,
    transcript: dict[str, Any],
    *,
    voices: VoiceMap,
    glossary: Glossary,
    settings: Settings,
    client: BodhanClient,
    logger: Any,
    cost: CostMeter,
    report: RunReport,
) -> set[str]:
    failed: set[str] = set()
    total_s = float(transcript.get("input", {}).get("duration_s") or transcript["stats"]["duration_s"])

    for lang in args.langs:
        try:
            existing = _read_segments(args.out, lang)
            if existing is None:
                raise BodhanDubError(
                    f"no translation found for {lang}; run the translate stage first"
                )
            voice, voice_warnings = voices.resolve(lang, preferred=existing.get("voice"))
            for warning in voice_warnings:
                report.warn(warning)

            refine = _make_refiner(
                lang,
                settings=settings,
                client=client,
                glossary=glossary,
                code_mixed=args.code_mixed,
                logger=logger,
            )

            out_dir = args.out / lang
            out_dir.mkdir(parents=True, exist_ok=True)
            spoken = speak_stage.speak_segments(
                existing["segments"],
                lang=lang,
                voice=voice,
                settings=settings,
                client=client,
                logger=logger,
                out_dir=out_dir,
                total_duration_s=total_s,
                refine=refine,
            )

            existing.update(
                {
                    "voice": voice,
                    "segments": spoken["segments"],
                    "stats": {**existing.get("stats", {}), **spoken["stats"]},
                }
            )
            _write_segments(args.out, lang, existing)

            stats = spoken["stats"]
            report.set_language(
                lang,
                "ok" if stats["coverage"] >= 0.98 else "partial",
                segments=stats["segments"],
                dubbed=stats["ok"],
                skipped=stats["skipped"],
                failed=stats["failed"],
                coverage=stats["coverage"],
                mean_cer=stats["mean_cer"],
                max_tempo=stats["max_tempo"],
                qa_failures=stats["qa_failures"],
                sync_misses=len(stats["misses"]),
                audio=stats["timeline"],
            )
            _update_targets(report, stats)
            event(
                logger,
                "speak.done",
                stage="speak",
                language=lang,
                segments=stats["segments"],
                dubbed=stats["ok"],
                skipped=stats["skipped"],
                failed=stats["failed"],
                coverage=stats["coverage"],
                mean_cer=stats["mean_cer"],
                max_tempo=stats["max_tempo"],
                qa_failures=stats["qa_failures"],
                out=str(spoken["timeline"]),
            )
            if stats["failed"] or stats["skipped"]:
                event(
                    logger,
                    "speak.incomplete",
                    level=40,
                    stage="speak",
                    language=lang,
                    skipped=stats["skipped"],
                    failed=stats["failed"],
                )
        except Exception as exc:
            failed.add(lang)
            report.set_language(lang, "speak_failed", error=f"{type(exc).__name__}: {exc}"[:300])
            event(
                logger,
                "speak.failed",
                level=40,
                stage="speak",
                language=lang,
                error=str(exc)[:200],
            )
    return failed


def _run_assemble(
    args: RunArgs,
    transcript: dict[str, Any],
    *,
    settings: Settings,
    logger: Any,
    report: RunReport,
) -> set[str]:
    failed: set[str] = set()
    total_s = float(
        transcript.get("input", {}).get("duration_s") or transcript["stats"]["duration_s"]
    )

    for lang in args.langs:
        try:
            existing = _read_segments(args.out, lang)
            if existing is None:
                raise BodhanDubError(
                    f"no dubbed segments for {lang}; run --stage speak first"
                )
            out_dir = args.out / lang
            dub_path = out_dir / f"audio.{lang}.wav"
            if not dub_path.exists():
                raise BodhanDubError(f"dub track missing for {lang}: {dub_path}")

            bed_path = out_dir / f"bed.{lang}.wav"
            bed.build_bed(
                args.input,
                transcript["segments"],
                out_path=bed_path,
                cache_dir=settings.cache_dir,
                logger=logger,
            )

            speech_path = out_dir / f"speech.{lang}.wav"
            loudness = mix.normalize_loudness(dub_path, speech_path)

            mixed_path = out_dir / f"mix.{lang}.wav"
            mixed = mix.mix(speech_path, bed_path, mixed_path)
            final_loudness = mix.measure_loudness(mixed_path)

            video_path = out_dir / f"video.{lang}.mp4"
            subtitles_path = out_dir / f"subtitles.{lang}.srt"
            cues = subtitles.write_srt(subtitles_path, existing["segments"])

            mux_info = mux.mux(
                args.input,
                mixed_path,
                video_path,
                duration_s=total_s,
                subtitle_path=subtitles_path,
                subtitle_language=languages.iso3(lang),
                burn_subtitles=args.burn_subtitles,
            )
            length = mux.verify_duration(video_path, total_s)
            subtitle_track = mux.has_subtitle_track(video_path)

            ok = bool(length["within_tolerance"] and loudness.get("within_target", False))
            report.set_language(
                lang,
                "ok" if ok else "partial",
                video=str(video_path.relative_to(args.out)),
                subtitles=str(subtitles_path.relative_to(args.out)),
                subtitles_mode=mux_info["subtitles"],
                subtitle_track=subtitle_track,
                duration_check=length,
                loudness=loudness,
                mix_loudness=round(final_loudness.get("lufs") or 0.0, 2),
                mux=mux_info,
                cues=cues,
            )
            _update_assemble_targets(report, loudness, length)
            event(
                logger,
                "assemble.done",
                stage="assemble",
                language=lang,
                video=str(video_path),
                duration_delta_ms=length["delta_ms"],
                lufs=loudness.get("after_lufs"),
                bed_gain_db=mixed["bed_gain_db"],
                limited=mixed["limited"],
                cues=cues,
                mux=mux_info["video"],
            )
        except Exception as exc:
            failed.add(lang)
            report.set_language(lang, "assemble_failed", error=f"{type(exc).__name__}: {exc}"[:300])
            event(
                logger,
                "assemble.failed",
                level=40,
                stage="assemble",
                language=lang,
                error=str(exc)[:200],
            )
    return failed


def _make_refiner(
    lang: str,
    *,
    settings: Settings,
    client: BodhanClient,
    glossary: Glossary,
    code_mixed: bool,
    logger: Any,
) -> Any:
    """Build the English-condensing callback, or None when no LLM is configured."""
    if settings.llm is None or not settings.llm.enabled:
        return None

    llm = LLMClient(settings.llm, logger=logger)
    keep = [term.en for term in glossary.terms]
    script = "codemix" if code_mixed else None

    def refine(source_en: str, *, budget_s: float, actual_s: float) -> str | None:
        if budget_s <= 0 or actual_s <= 0:
            return None
        # Aim a little under the slot: TTS length is roughly linear in characters.
        target = int(len(source_en) * (budget_s / actual_s) * 0.92)
        condensed = llm.condense(source_en, target_chars=target, keep=keep)
        if not condensed:
            return None
        result = client.translate(
            condensed,
            target_language=lang,
            script=script,
            stage="refine",
        )
        translation = str(result.data.get("translation", "")).strip()
        return translation or None

    return refine


def _update_assemble_targets(
    report: RunReport,
    loudness: dict[str, Any],
    length: dict[str, Any],
) -> None:
    targets = report.data["quality_targets"]

    if loudness.get("after_lufs") is not None:
        targets["loudness"]["observed"] = (
            f"{loudness['after_lufs']:.2f} LUFS (target -16 +/- 1)"
        )
        targets["loudness"]["status"] = "pass" if loudness.get("within_target") else "partial"
    else:
        targets["loudness"]["observed"] = "could not be measured"
        targets["loudness"]["status"] = "partial"

    targets["total_length"]["observed"] = (
        f"{length['duration_s']:.3f}s vs {length['expected_s']:.3f}s "
        f"({length['delta_ms']:+.0f} ms, tolerance +/-{length['tolerance_ms']} ms)"
    )
    targets["total_length"]["status"] = "pass" if length["within_tolerance"] else "fail"


def _update_targets(report: RunReport, stats: dict[str, Any]) -> None:
    targets = report.data["quality_targets"]
    targets["coverage"]["observed"] = f"{stats['coverage']:.1%} of segments dubbed"
    targets["coverage"]["status"] = "pass" if stats["coverage"] >= 0.98 else "partial"
    if stats["mean_cer"] is not None:
        targets["content_fidelity"]["observed"] = f"mean CER {stats['mean_cer']:.3f}"
        targets["content_fidelity"]["status"] = "pass" if stats["mean_cer"] <= 0.25 else "partial"
    if stats["max_tempo"] is not None:
        targets["speed_change"]["observed"] = (
            f"max time-stretch {stats['max_tempo']:.2f}x, "
            f"{stats['speed_violations']} outside 0.85-1.20"
        )
        targets["speed_change"]["status"] = "pass" if stats["speed_violations"] == 0 else "fail"
    targets["segment_sync"]["observed"] = (
        f"clips placed at source start; leading silence trimmed by a mean of "
        f"{stats['mean_leading_silence_trimmed_s']:.3f}s"
    )
    targets["segment_sync"]["status"] = "partial"
    if stats["misses"]:
        targets["segment_sync"]["observed"] += f"; {len(stats['misses'])} overran their slot"


# -- files ----------------------------------------------------------------


def _segments_path(out: Path, lang: str) -> Path:
    return out / lang / f"segments.{lang}.json"


def _write_segments(out: Path, lang: str, payload: dict[str, Any]) -> None:
    path = _segments_path(out, lang)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"schema": SEGMENTS_SCHEMA, **payload}
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_segments(out: Path, lang: str) -> dict[str, Any] | None:
    path = _segments_path(out, lang)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _mark_failed(report: RunReport, stage: str | None, exc: BaseException) -> None:
    if stage:
        report.stage(stage, "failed", error=f"{type(exc).__name__}: {exc}")


def _print_summary(args: RunArgs, report: RunReport) -> None:
    data = report.data
    transcript = data.get("transcript") or {}
    print("")
    print("  dubbing complete")
    print(f"    input     {args.input}")
    print(f"    segments  {transcript.get('segments', 0)}")
    print(f"    coverage  {transcript.get('speech_coverage', 0):.1%} of the file is speech")
    for lang in args.langs:
        entry = data["languages"].get(lang, {})
        name = languages.name(lang)
        print(
            f"    {lang} {name:<10} "
            f"{entry.get('dubbed', 0)}/{entry.get('segments', 0)} dubbed, "
            f"coverage {entry.get('coverage', 0):.1%}"
        )
        if entry.get("video"):
            print(f"       video     {args.out / entry['video']}")
        if entry.get("subtitles"):
            print(f"       subtitles {args.out / entry['subtitles']}")
    print(f"    cost      INR {data.get('cost', {}).get('total_inr', 0):.4f}")
    print(f"    output    {report.path.parent}")
    print("")
