"""Pipeline entry point and checkpoint orchestration."""

import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import asdict
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from shutil import which
from time import perf_counter

import httpx

from .audio.export import AUDIO_EXPORT_POLICY_VERSION, AudioExportError, export_audio
from .audio.narration import NARRATION_POLICY_VERSION, SCRIPT_POLICY_VERSION, build_script, plan_narration
from .audio.speak import IndicSpeakProvider, LocalSpeak, synthesize_chunk, synthesize_script
from .audio.speech_qa import SPEECH_QA_POLICY_VERSION, verify_script
from .audio.transcribe import IndicTranscribeProvider, LocalTranscribe, Transcription
from .checkpoints import (
    CheckpointStore, LegacyCheckpointUnavailable, clear_output, import_legacy_scripts, import_legacy_speech,
    input_identity, output_lock, write_atomic_json,
)
from .config import LessonRequest, backend_mode, load_voices, speech_cer_threshold, speech_input_limit
from .inference import Inference, ProviderError
from .llm import classify_language, propose_figure_narration, propose_reading_order
from .models import (
    Attempt, AudioRun, BoundaryDecision, LanguageDecision, NarrationPlan, NarrationScript, NarrationScripts, OCRPage, Page, PreprocessedPage,
    ScriptChunk, SourceChapter, SpeechCandidate, SpeechQARun, SpeechRun, TargetAudio, TargetSpeechQA, TranslatedUnit,
    TranslationPlan, TranslationQARun, TranslationRun,
)
from .ocr.assembly import build_chapter, decide_boundaries
from .ocr.extraction import extract_pages
from .ocr.inputs import discover_pages
from .ocr.language import resolve_language, sample_language
from .ocr.layout import repair_layout
from .ocr.preprocess import PreprocessSettings, preprocess_pages
from .ocr.provider import IndicOCRProvider, LocalOCR
from .ocr.render import render_markdown
from .report import AttemptLog, RunReport
from .translation.provider import (
    GGUF_CONTEXT_LIMIT, GGUF_MODEL, GGUFTranslate, IndicTranslateProvider, LocalTranslate, Translate,
    TranslationResponse, gguf_endpoint,
)
from .translation.segments import CONTEXT_LIMIT, load_glossary, plan_units
from .translation.translate import check_structure, translate_plan

HTTP_TIMEOUT_SECONDS = 60
CHECKPOINTS = (
    "language", "preprocess", "ocr", "layout", "boundaries", "source", "segments", "translate", "translation-qa",
    "narration-plan", "scripts", "speech", "speech-qa", "audio",
)


@contextmanager
def _open_ocr(
    env: Mapping[str, str], inference: Inference,
) -> Iterator[tuple[httpx.Client, IndicOCRProvider]]:
    mode = backend_mode(env)
    local = LocalOCR()
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, limits=httpx.Limits(max_connections=1)) as client:
        try:
            yield client, IndicOCRProvider(mode, inference, client, env, local, local.release_after_oom)
        finally:
            with suppress(Exception):
                local.close()


@contextmanager
def _open_translate(
    env: Mapping[str, str], inference: Inference, gguf: httpx.URL | None,
) -> Iterator[Translate]:
    mode = backend_mode(env)
    with ExitStack() as resources:
        client = resources.enter_context(httpx.Client(timeout=HTTP_TIMEOUT_SECONDS,
                                                      limits=httpx.Limits(max_connections=1)))
        if gguf is not None:
            local_client = resources.enter_context(httpx.Client(
                timeout=httpx.Timeout(120, connect=3), limits=httpx.Limits(max_connections=1),
                trust_env=False,
            ))
            # The separately managed llama-server owns its worker and allocations.
            provider = IndicTranslateProvider(
                mode, inference, client, env, GGUFTranslate(local_client, gguf), lambda: None,
                local_model=GGUF_MODEL,
            )
        else:
            local = LocalTranslate()

            @resources.callback
            def close_local() -> None:
                with suppress(Exception):
                    local.close()

            provider = IndicTranslateProvider(mode, inference, client, env, local, local.release_after_oom)
        yield provider.translate


def _write_json(path: Path, record: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def _translation_attempt_ids(units: tuple[TranslatedUnit, ...]) -> set[str]:
    return {attempt_id for unit in units for leg in unit.legs for attempt_id in leg.attempt_ids}


def _write_with_attempts(path: Path, record: dict, attempts: AttemptLog, ids: set[str]) -> None:
    record["attempts"] = [asdict(attempt) for attempt in attempts.attempts if attempt.id in ids]
    _write_json(path, record)


def _write_pivot(
    out: Path, plan: TranslationPlan, translation: TranslationRun, attempts: AttemptLog,
    repairs: tuple[TranslatedUnit, ...] = (),
) -> None:
    if translation.pivots or repairs:
        record = {
            "source_lang": plan.source_lang, "target_lang": "en",
            "units": [asdict(unit) for unit in translation.pivots],
        }
        if repairs:
            record["repairs"] = [asdict(unit) for unit in repairs]
        _write_with_attempts(out / "work" / "pivot.en.json", record, attempts,
                             _translation_attempt_ids(translation.pivots + repairs))
    else:
        (out / "work" / "pivot.en.json").unlink(missing_ok=True)


def _run_translation(
    plan: TranslationPlan, request: LessonRequest, attempts: AttemptLog, translate: Translate,
) -> TranslationRun:
    result = translate_plan(plan, request.target_languages, translate)
    _write_pivot(request.out, plan, result, attempts)
    shared_ids = _translation_attempt_ids(result.pivots)
    for target in result.targets:
        _write_with_attempts(request.out / target.target_lang / "segments.json", asdict(target),
                             attempts, _translation_attempt_ids(target.units) - shared_ids)
    return result


def _run_translation_qa(
    chapter: SourceChapter, plan: TranslationPlan, result: TranslationRun, request: LessonRequest,
    attempts: AttemptLog, translate: Translate,
) -> TranslationQARun:
    qa = check_structure(chapter, plan, result, translate)
    _write_pivot(request.out, plan, result, attempts, qa.pivot_repairs)
    shared_repair_ids = _translation_attempt_ids(qa.pivot_repairs) - _translation_attempt_ids(result.pivots)
    for lesson in qa.lessons:
        language = lesson.qa.target_lang
        directory = request.out / language
        retry_ids = {attempt_id for section in lesson.qa.sections for unit in section.units
                     for attempt_id in unit.retry_attempt_ids}
        _write_with_attempts(directory / "translation-qa.json", asdict(lesson.qa),
                             attempts, retry_ids - shared_repair_ids)
        (directory / f"lesson.{language}.md").write_text(lesson.markdown, encoding="utf-8")
    return qa


def _run_narration_plan(
    chapter: SourceChapter, translation: TranslationQARun, env: Mapping[str, str], inference: Inference, out: Path,
) -> NarrationPlan:
    attempts = inference.attempts.attempts
    attempt_start = len(attempts)
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, limits=httpx.Limits(max_connections=1)) as client:
        propose_figure = None
        if all(env.get(name) for name in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")):
            propose_figure = partial(propose_figure_narration, client, inference, env)
        narration = plan_narration(chapter, translation, propose_figure=propose_figure)
    record = asdict(narration)
    record["attempts"] = [asdict(attempt) for attempt in attempts[attempt_start:]]
    (out / "work" / "narration-plan.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return narration


def _run_scripts(
    chapter: SourceChapter, narration: NarrationPlan, plan: TranslationPlan, translate: Translate,
    attempts: AttemptLog, out: Path, voices: Mapping[str, str], max_chars: int,
) -> NarrationScripts:
    scripts = []
    for target in narration.targets:
        attempt_start = len(attempts.attempts)
        script = build_script(chapter, target, plan, translate, voice=voices.get(target.target_lang),
                              max_chars=max_chars)
        record = asdict(script)
        record["attempts"] = [asdict(attempt) for attempt in attempts.attempts[attempt_start:]]
        (out / script.language / f"narration_script.{script.language}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        scripts.append(script)
    return NarrationScripts(tuple(scripts))


@contextmanager
def _open_speak(
    env: Mapping[str, str], inference: Inference,
) -> Iterator[IndicSpeakProvider]:
    local = LocalSpeak()
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, limits=httpx.Limits(max_connections=1)) as client:
        try:
            yield IndicSpeakProvider(backend_mode(env), inference, client, env, local, local.release_after_oom)
        finally:
            with suppress(Exception):
                local.close()


def _run_speech(
    scripts: NarrationScripts, out: Path, env: Mapping[str, str], inference: Inference, store: CheckpointStore,
) -> SpeechRun:
    attempts = inference.attempts.attempts
    targets = []
    with _open_speak(env, inference) as provider:
        synthesize = partial(_checkpoint_synthesis, out=out, provider=provider, store=store)
        for script in scripts.scripts:
            first_attempt = len(attempts)
            target = synthesize_script(script, out, provider, synthesize=synthesize)
            targets.append(target)
            record = asdict(target)
            record["attempts"] = [asdict(attempt) for attempt in attempts[first_attempt:]]
            _write_json(out / script.language / "chunks.json", record)
    return SpeechRun(tuple(targets))


def _speech_candidate_paths(candidate: SpeechCandidate) -> Iterator[str]:
    if candidate.audio_path:
        yield candidate.audio_path


def _checkpoint_synthesis(
    chunk: ScriptChunk, language: str, voice: str, out: Path, provider: IndicSpeakProvider, store: CheckpointStore,
) -> SpeechCandidate:
    return store.item(
        ("synthesis", language, voice, chunk.id, chunk.text), SpeechCandidate,
        lambda: synthesize_chunk(chunk, language, voice, out, provider), _speech_candidate_paths,
    )


def _run_speech_qa(
    scripts: NarrationScripts, speech: SpeechRun, out: Path, env: Mapping[str, str],
    inference: Inference, threshold: float, store: CheckpointStore,
    previous: tuple[SpeechQARun, tuple[Attempt, ...]] | None = None,
) -> SpeechQARun:
    local = LocalTranscribe()
    attempts = inference.attempts.attempts
    prior_targets = {}
    prior_attempts = ()
    if previous is not None:
        old_qa, prior_attempts = previous
        prior_targets = {target.language: target for target in old_qa.targets}
        attempts.extend(prior_attempts)
        if store.progress is not None:
            store.progress("[speech-qa] Rechecking saved transcripts with the updated Tamil comparisons.")
    targets = []
    with _open_speak(env, inference) as speak, httpx.Client(
        timeout=HTTP_TIMEOUT_SECONDS, limits=httpx.Limits(max_connections=1)
    ) as client:
        asr = IndicTranscribeProvider(backend_mode(env), inference, client, env, local, local.release_after_oom)
        synthesize = partial(_checkpoint_synthesis, out=out, provider=speak, store=store)

        def transcribe(candidate: SpeechCandidate, language: str) -> Transcription:
            return store.item(
                ("transcription", language, candidate.chunk_id, candidate.attempt_id, candidate.audio_path),
                Transcription, lambda: asr.transcribe(candidate, language, out),
            )

        try:
            for script, candidates in zip(scripts.scripts, speech.targets, strict=True):
                if store.progress is not None:
                    store.progress(f"[speech-qa] Checking {script.language} ({len(candidates.chunks)} chunks).")
                first_attempt = len(attempts)
                target = verify_script(script, candidates, synthesize, transcribe, threshold,
                                       previous=prior_targets.get(script.language))
                targets.append(target)
                record = asdict(target)
                language_attempts = tuple(attempt for attempt in prior_attempts
                                          if len(attempt.entity_ids) > 1 and attempt.entity_ids[1] == script.language)
                record["attempts"] = [asdict(attempt) for attempt in (*language_attempts, *attempts[first_attempt:])]
                _write_json(out / script.language / "speech-qa.json", record)
                if store.progress is not None:
                    metrics = target.metrics
                    store.progress(f"[speech-qa] {script.language}: verified {metrics.measured_chunks}"
                                   f"/{metrics.total_chunks} chunks.")
        finally:
            with suppress(Exception):
                local.close()
    return SpeechQARun(tuple(targets))


def _artifact_paths(stage: str, result) -> Iterator[str]:
    if stage in ("ocr", "layout"):
        yield from (f"source/pages/{page.page_id}.json" for page in result)
    if stage == "preprocess":
        for page in result:
            yield f"work/images/{page.page_id}.transform.json"
            if page.original_path:
                yield page.original_path
            if page.normalized_path:
                yield page.normalized_path
    if stage == "translate":
        yield from (f"{target.target_lang}/segments.json" for target in result.targets)
        if result.pivots:
            yield "work/pivot.en.json"
    if stage == "translation-qa":
        for lesson in result.lessons:
            language = lesson.qa.target_lang
            yield f"{language}/translation-qa.json"
            yield f"{language}/lesson.{language}.md"
        if result.pivot_repairs:
            yield "work/pivot.en.json"
    if stage in ("scripts", "legacy-scripts"):
        for script in result.scripts:
            yield f"{script.language}/narration_script.{script.language}.json"
    if stage == "speech":
        for target in result.targets:
            yield f"{target.language}/chunks.json"
            yield from (chunk.audio_path for chunk in target.chunks if chunk.audio_path)
    if stage == "speech-qa":
        for target in result.targets:
            yield f"{target.language}/speech-qa.json"
            for section in target.sections:
                for chunk in section.chunks:
                    yield from (review.candidate.audio_path for review in chunk.attempts
                                if review.candidate.audio_path)
    if stage == "audio":
        for target in result.targets:
            yield f"{target.language}/narration_script.{target.language}.json"
            yield target.audio_path or f"{target.language}/lesson.{target.language}.mp3"
            yield from (section.audio_path for section in target.sections if section.audio_path)
    yield from {
        "language": ("source/language.json",),
        "layout": ("work/layout.json",),
        "boundaries": ("work/boundaries.json",),
        "source": ("work/chapter.json", "source/lesson.src.md"),
        "segments": ("work/translation-plan.json",),
        "narration-plan": ("work/narration-plan.json",),
    }.get(stage, ())


def _export_target(script: NarrationScript, target_qa: TargetSpeechQA, out: Path) -> TargetAudio:
    target = export_audio(script, target_qa, out)
    language = script.language
    artifact = out / language / f"narration_script.{language}.json"
    record = asdict(script)
    record["attempts"] = json.loads(artifact.read_text(encoding="utf-8")).get("attempts", [])
    qa_path = f"{language}/speech-qa.json"
    audio = asdict(target)
    audio_sections = audio.pop("sections")
    audio["speech_qa"] = qa_path
    audio["speech_qa_coverage"] = target_qa.metrics.coverage
    record["audio"] = audio
    qa_sections = {section.section_id: section for section in target_qa.sections}
    for section, exported in zip(record["sections"], audio_sections, strict=True):
        section.update(exported)
        section_qa = qa_sections.get(section["section_id"])
        section["speech_qa"] = qa_path
        section["speech_qa_metrics"] = asdict(section_qa.metrics) if section_qa else None
        reviewed = {chunk.chunk_id: chunk for chunk in section_qa.chunks} if section_qa else {}
        for chunk in section["chunks"]:
            review = reviewed.get(chunk["id"])
            accepted = review.accepted_candidates if review else ()
            chunk["speech_qa"] = qa_path
            chunk["accepted_candidate_ids"] = review.accepted_candidate_ids if review else ()
            durations = tuple(candidate.duration_seconds for candidate in accepted)
            chunk["duration_seconds"] = sum(durations) if durations and all(
                duration is not None for duration in durations
            ) else None
    write_atomic_json(artifact, record)
    return target


def _run_audio(
    scripts: NarrationScripts, qa: SpeechQARun, out: Path,
    on_target: Callable[[TargetAudio], None] | None = None,
) -> AudioRun:
    targets = []
    for script, target_qa in zip(scripts.scripts, qa.targets, strict=True):
        try:
            target = _export_target(script, target_qa, out)
        except (OSError, AudioExportError):
            target = TargetAudio(script.language, "failed", (),
                                 reason="Could not publish this language's audio artifacts.")
        targets.append(target)
        if on_target is not None:
            on_target(target)
    return AudioRun(tuple(targets))


def _run_source(
    canonical: tuple[OCRPage, ...], decisions: tuple[BoundaryDecision, ...], language: str | None, out: Path,
) -> SourceChapter:
    chapter = build_chapter(canonical, decisions, language)
    _write_json(out / "work/chapter.json", asdict(chapter))
    (out / "source/lesson.src.md").write_text(render_markdown(chapter), encoding="utf-8")
    return chapter


def run_lesson(
    request: LessonRequest, env: Mapping[str, str], *,
    progress: Callable[[str], None] | None = None,
    confirm_resume: Callable[[Path], bool] | None = None,
) -> (
    LanguageDecision | tuple[PreprocessedPage, ...] | tuple[OCRPage, ...]
    | tuple[BoundaryDecision, ...] | SourceChapter | TranslationPlan | TranslationRun | TranslationQARun
    | NarrationPlan | NarrationScripts | SpeechRun | SpeechQARun | AudioRun
):
    started = perf_counter()
    override = resolve_language(request.source_lang, ()) if request.source_lang is not None else None
    pages = discover_pages(request.input_path)
    identity = input_identity(request.input_path, pages)
    last_stage = CHECKPOINTS.index(request.until) if request.until else len(CHECKPOINTS) - 1
    mode = backend_mode(env) if override is None or last_stage >= CHECKPOINTS.index("ocr") else None
    llm_settings = (env.get("LLM_BASE_URL"), env.get("LLM_MODEL"), bool(env.get("LLM_API_KEY")))
    glossary = None
    gguf = None
    if last_stage >= CHECKPOINTS.index("segments"):
        if request.glossary is not None:
            glossary = load_glossary(request.glossary)
        gguf_url = env.get("BODHAN_TRANSLATE_GGUF_URL")
        if gguf_url and mode == "local-first":
            gguf = gguf_endpoint(gguf_url)
    context_limit = GGUF_CONTEXT_LIMIT if gguf is not None else CONTEXT_LIMIT
    glossary_settings = tuple((glossary or {}).items())
    voices = {}
    max_chars = None
    if last_stage >= CHECKPOINTS.index("scripts"):
        max_chars = speech_input_limit(env)
        if request.voices is not None:
            voices = load_voices(request.voices)
    threshold = speech_cer_threshold(env) if last_stage >= CHECKPOINTS.index("speech-qa") else None
    selected_voices = {language: voices.get(language) for language in request.target_languages}
    out = request.out
    event_path = out / "events.jsonl"
    attempts = AttemptLog()
    report = None
    if request.until is None:
        assert mode is not None
        report = RunReport(pages, request.target_languages, mode, attempts)

    def event(name: str, **fields) -> None:
        if report is not None:
            line = json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(), "event": name,
                "stage": report.current_stage, **fields,
            }, ensure_ascii=False)
            with event_path.open("a", encoding="utf-8") as log:
                log.write(line + "\n")
            if progress is not None:
                progress(line)

    def publish_report() -> None:
        if report is not None:
            write_atomic_json(out / "report.json", report.snapshot(perf_counter() - started))

    def log_attempt(attempt: Attempt, *, reused: bool = False) -> None:
        assert report is not None
        report.attempt_stages[attempt.id] = report.current_stage
        event("attempt", attempt_id=attempt.id, entity_ids=attempt.entity_ids, service=attempt.service,
              backend=attempt.backend, result=attempt.result, error_category=attempt.error_category,
              attempt_number=attempt.number, elapsed_ms=attempt.elapsed_ms, reused=reused)

    def on_attempt(attempt: Attempt) -> None:
        assert report is not None
        report.live_attempt_ids.add(attempt.id)
        log_attempt(attempt)

    if report is not None:
        attempts.on_record = on_attempt
    inference = Inference(attempts, progress=progress)
    store = CheckpointStore(out, identity, attempts, progress)
    language_settings = override.code if override is not None else (mode, llm_settings)

    def restart() -> None:
        nonlocal store
        clear_output(request, pages)
        attempts.attempts.clear()
        store = CheckpointStore(out, identity, attempts, progress)
        publish_report()
        event("run_started", languages=request.target_languages)
        if progress is not None:
            progress("Starting over with cleared generated output.")

    @contextmanager
    def audit() -> Iterator[None]:
        publish_report()
        if report is not None:
            event_path.write_text("", encoding="utf-8")
            event("run_started", languages=request.target_languages)
        try:
            yield
        except (OSError, ValueError, ProviderError, AudioExportError, KeyboardInterrupt) as error:
            if report is not None:
                interrupted = isinstance(error, KeyboardInterrupt)
                reason = (
                    "Run interrupted." if interrupted else
                    str(error) if isinstance(error, ProviderError) else
                    f"The {report.current_stage} stage could not complete ({type(error).__name__})."
                )
                report.fail(type(error).__name__, reason, interrupted=interrupted)
                publish_report()
                event("run_finished", status=report.status)
            if isinstance(error, (ProviderError, AudioExportError)):
                raise ValueError(str(error)) from None
            raise

    def checkpoint(stage, result_type, compute, settings=None):
        if report is None:
            return store.run(stage, result_type, settings, compute, partial(_artifact_paths, stage))
        report.current_stage = stage
        stage_started = perf_counter()
        first = len(attempts.attempts)
        reused = True

        def run_stage():
            nonlocal reused
            reused = False
            return compute()

        event("stage_started")
        try:
            result = store.run(stage, result_type, settings, run_stage, partial(_artifact_paths, stage))
        finally:
            for attempt in attempts.attempts[first:]:
                if attempt.id not in report.live_attempt_ids:
                    log_attempt(attempt, reused=True)
        report.record_stage(stage, result, perf_counter() - stage_started, reused)
        if stage in ("preprocess", "ocr", "layout"):
            for page in result:
                event("page_outcome", page_id=page.page_id, status=page.status, reason=page.reason)
        if isinstance(result, TranslationQARun):
            for lesson in result.lessons:
                for section in lesson.qa.sections:
                    event("section_outcome", language=lesson.qa.target_lang, section_id=section.section_id,
                          status=section.status, source_block_ids=report.source_sections[section.section_id])
        if isinstance(result, NarrationScripts):
            for script in result.scripts:
                for section in script.sections:
                    for chunk in section.chunks:
                        event("script_chunk", language=script.language, section_id=section.section_id,
                              chunk_id=chunk.id, unit_ids=chunk.unit_ids, source_block_ids=chunk.source_block_ids)
        if isinstance(result, SpeechQARun):
            for target in result.targets:
                for section in target.sections:
                    event("section_outcome", language=target.language, section_id=section.section_id,
                          cer=section.metrics.cer, coverage=section.metrics.coverage, reason=section.reason)
        if isinstance(result, AudioRun):
            for target in result.targets:
                for section in target.sections:
                    event("section_outcome", language=target.language, section_id=section.section_id,
                          status=section.coverage_status)
                event("language_outcome", language=target.language, status=target.status)
        publish_report()
        event("stage_finished", reused=reused)
        return result

    def finish_scripts(
        scripts: NarrationScripts, *, legacy: bool = False,
    ) -> NarrationScripts | SpeechRun | SpeechQARun | AudioRun:
        if request.until == "scripts":
            return scripts

        def run_speech():
            if legacy and mode is not None:
                try:
                    return import_legacy_speech(scripts, out, mode, attempts)
                except LegacyCheckpointUnavailable:
                    pass
            return _run_speech(scripts, out, env, inference, store)

        speech = checkpoint("speech", SpeechRun, run_speech, mode)
        if request.until == "speech":
            return speech
        assert threshold is not None
        previous_qa = None
        if any(script.language == "ta" for script in scripts.scripts):
            previous_qa = store.previous_checkpoint(
                "speech-qa", SpeechQARun, (threshold, 2), partial(_artifact_paths, "speech-qa"),
            )
        qa = checkpoint("speech-qa", SpeechQARun,
                        lambda: _run_speech_qa(scripts, speech, out, env, inference, threshold, store, previous_qa),
                        (threshold, SPEECH_QA_POLICY_VERSION))
        if request.until == "speech-qa":
            return qa

        def on_target(target: TargetAudio) -> None:
            assert report is not None
            report.record_audio(target)
            publish_report()

        audio = checkpoint("audio", AudioRun, lambda: _run_audio(scripts, qa, out, on_target if report else None),
                           AUDIO_EXPORT_POLICY_VERSION)
        if report is not None:
            event("run_finished", status=report.status)
        return audio

    with output_lock(request, pages), audit(), ExitStack() as resources:
        if last_stage >= CHECKPOINTS.index("audio") and any(which(tool) is None for tool in ("ffmpeg", "ffprobe")):
            raise ValueError("Install FFmpeg and ensure ffmpeg and ffprobe are on PATH before audio export.")
        start_over = request.force
        if start_over and progress is not None:
            progress("Starting over with cleared generated output.")
        if not start_over and confirm_resume is not None and store.has_checkpoint(
            "language", LanguageDecision, language_settings, partial(_artifact_paths, "language"),
        ) and not confirm_resume(out):
            restart()
            start_over = True
        # The full report needs typed page, source and translation checkpoints.
        if report is None and max_chars is not None and mode is not None and not start_over and not (
            store.directory / "language.json"
        ).exists():
            settings = (request.target_languages, override.code if override is not None else None,
                        glossary_settings, context_limit, str(gguf) if gguf is not None else None,
                        selected_voices, max_chars, mode, llm_settings, SCRIPT_POLICY_VERSION)
            try:
                scripts = checkpoint(
                    "legacy-scripts", NarrationScripts,
                    lambda: import_legacy_scripts(request, pages, override, glossary, context_limit, mode,
                                                  selected_voices, max_chars, attempts),
                    settings,
                )
            except LegacyCheckpointUnavailable:
                pass
            else:
                if confirm_resume is None or confirm_resume(out):
                    return finish_scripts(scripts, legacy=True)
                restart()
        ocr_connection = None

        def ocr_resources():
            nonlocal ocr_connection
            if ocr_connection is None:
                ocr_connection = resources.enter_context(_open_ocr(env, inference))
            return ocr_connection

        def run_language():
            language = override
            if language is None:
                client, provider = ocr_resources()
                classify = partial(classify_language, client, inference, env)
                language = sample_language(pages, provider.ocr, classify, attempts)
            _write_json(out / "source/language.json", asdict(language))
            return language

        language = checkpoint("language", LanguageDecision, run_language, language_settings)
        if request.until == "language":
            return language
        prepared = checkpoint("preprocess", tuple[PreprocessedPage, ...],
                              lambda: preprocess_pages(pages, out, PreprocessSettings(language.code)))
        if request.until == "preprocess":
            return prepared

        def run_ocr():
            _, provider = ocr_resources()

            def save_page(page_id, read):
                page = store.item(page_id, OCRPage, read)
                if report is not None:
                    report.record_page(page)
                return page

            return extract_pages(pages, prepared, out, provider.ocr, attempts, save_page=save_page)

        extracted = checkpoint("ocr", tuple[OCRPage, ...], run_ocr, mode)
        if request.until == "ocr":
            return extracted

        def run_layout():
            client, provider = ocr_resources()
            propose = partial(propose_reading_order, client, inference, env)
            return repair_layout(pages, prepared, extracted, out, propose, provider.ocr, attempts)

        canonical = checkpoint("layout", tuple[OCRPage, ...], run_layout, (mode, llm_settings))
        if request.until == "layout":
            return canonical
        resources.close()

        def run_boundaries():
            decisions = decide_boundaries(canonical)
            _write_json(out / "work/boundaries.json", [asdict(decision) for decision in decisions])
            return decisions

        decisions = checkpoint("boundaries", tuple[BoundaryDecision, ...], run_boundaries)
        if request.until == "boundaries":
            return decisions
        chapter = checkpoint("source", SourceChapter, lambda: _run_source(canonical, decisions, language.code, out))
        if request.until == "source":
            return chapter

        def run_segments():
            plan = plan_units(chapter, glossary, context_limit=context_limit)
            _write_json(out / "work/translation-plan.json", asdict(plan))
            return plan

        plan = checkpoint("segments", TranslationPlan, run_segments, (glossary_settings, context_limit))
        if request.until == "segments":
            return plan

        translate = None

        def translate_text(unit_id: str, source: str, target: str, text: str):
            def call() -> TranslationResponse:
                nonlocal translate
                if translate is None:
                    translate = resources.enter_context(_open_translate(env, inference, gguf))
                return translate(unit_id, source, target, text)

            return store.item((unit_id, source, target, text), TranslationResponse, call)

        translation = checkpoint(
            "translate", TranslationRun, lambda: _run_translation(plan, request, attempts, translate_text),
            (request.target_languages, mode, str(gguf) if gguf is not None else None),
        )
        if request.until == "translate":
            return translation
        qa = checkpoint("translation-qa", TranslationQARun,
                        lambda: _run_translation_qa(chapter, plan, translation, request, attempts, translate_text))
        if request.until == "translation-qa":
            return qa
        narration = checkpoint("narration-plan", NarrationPlan,
                               lambda: _run_narration_plan(chapter, qa, env, inference, out),
                               (llm_settings, NARRATION_POLICY_VERSION))
        if request.until == "narration-plan":
            return narration
        assert max_chars is not None
        scripts = checkpoint(
            "scripts", NarrationScripts,
            lambda: _run_scripts(chapter, narration, plan, translate_text, attempts, out, voices, max_chars),
            (selected_voices, max_chars, SCRIPT_POLICY_VERSION),
        )
        resources.close()
        return finish_scripts(scripts)
