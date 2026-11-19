"""Run a task from locally scoped file references and record its outcome."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher
from itertools import batched
import json
from pathlib import Path
from time import monotonic
from typing import Callable, Protocol, cast, get_args
import unicodedata

from mcp.server.fastmcp.exceptions import ToolError

from bodhan_mcp.document_chunks import MAX_SOURCE_BYTES, markdown_for_export
from bodhan_mcp.ocr import OcrPage, read_document
from bodhan_mcp.request_budget import BudgetExhausted, RequestBudget, active_budget
from bodhan_mcp.server import Language, TranslationResult, translate_text
from bodhan_mcp.speech import SpeechLanguage, synthesize_speech
from bodhan_mcp.transcription import SpeechLanguage as TranscriptionLanguage
from bodhan_mcp.transcription import TranscriptionResult, transcribe_audio

from .clip_language import AUDIO_SUFFIXES, assess_transcript
from .planner import (
    Action, Declare, Deliverable, Finish, PlannerError, Read, Save, SaveSubtitles,
    Speak, Summarize, Transcribe, Translate, TranslateMatchingClips, TranslationReview,
)
from .subtitles import SUBTITLE_TIMING_NOTE, SUBTITLE_WINDOW_SECONDS, render_srt

MAX_SPEECH_BYTES = 16_000
REVIEW_BYTES = 6_000
PREVIEW_CHARS = 320
SUMMARY_WORDS_PER_MINUTE = 130
SUBTITLE_REVIEW_CUES = 4


class Planner(Protocol):
    def decide(self, state: dict) -> Action: ...
    def review(self, source: str, translation: str, language: str) -> TranslationReview: ...
    def summarize(self, source: str, task: str, target_words: int | None) -> str: ...
    def review_summary(self, source: str, summary: str) -> TranslationReview: ...


def _normalized(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFC", text).casefold()
        if unicodedata.category(char)[0] in "LMN"
    )


def _inputs(paths: list[Path]) -> dict[str, Path]:
    files = []
    for path in paths:
        if not path.exists():
            raise ToolError(f"Input does not exist: {path.name}")
        if not path.is_dir():
            files.append(path.resolve())
            continue
        root = path.resolve()
        files.extend(
            item.resolve()
            for item in sorted(root.iterdir())
            if not item.name.startswith(".") and item.is_file() and item.resolve().is_relative_to(root)
        )
    return {f"input-{i}": path for i, path in enumerate(dict.fromkeys(files), start=1)}


@dataclass
class StepTrace:
    number: int
    verification_seconds: float = 0.0
    verification_requests: set[int] = field(default_factory=set)


def _request_summary(requests: list[dict]) -> dict:
    return {
        "provider_requests": requests,
        "cost_inr": sum((request["cost_inr"] for request in requests), Decimal(0)),
    }


@dataclass
class Run:
    output: Path
    budget: RequestBudget
    task: str
    texts: dict[str, str] = field(default_factory=dict)
    text_languages: dict[str, str] = field(default_factory=dict)
    transcriptions: dict[str, TranscriptionResult] = field(default_factory=dict)
    subtitle_translations: dict[tuple[str, str], str] = field(default_factory=dict)
    deliverables: dict[str, Deliverable] = field(default_factory=dict)
    fulfilled: dict[str, str] = field(default_factory=dict)
    partial_artifacts: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    clip_selections: dict[str, dict] = field(default_factory=dict)
    verification: list[dict] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)

    def record(self, event: str, **fields: object) -> None:
        with (self.output / "trace.jsonl").open("a", encoding="utf-8") as trace:
            trace.write(json.dumps({"event": event, **fields}, ensure_ascii=False, default=str) + "\n")

    def remember(self, text: str, language: str | None = None) -> dict:
        if not text.strip():
            raise ToolError("The selected source returned no text.")
        reference = f"text-{len(self.texts) + 1}"
        self.texts[reference] = text
        if language is not None:
            self.text_languages[reference] = language
        return {"text_id": reference, "characters": len(text), "language": language,
                "preview": text[:PREVIEW_CHARS]}

    def target(self, reference: str, formats: set[str], language: str | None) -> str:
        requirement = self.deliverables.get(reference)
        if requirement is None:
            raise ToolError("Select a declared deliverable ID.")
        if reference in self.fulfilled:
            raise ToolError("This deliverable already has an artifact.")
        if requirement.format not in formats:
            raise ToolError("The selected operation cannot produce the declared format.")
        if requirement.language is not None and language != requirement.language:
            raise ToolError("The output language does not match the declared deliverable.")
        return f"artifacts/{reference}.{requirement.format}"

    def save_partial(self, path: str, text: str, **details: object) -> None:
        (self.output / path).write_text(text, encoding="utf-8")
        self.artifacts.append(path)
        self.partial_artifacts.append(path)
        self.record("partial_artifact", artifact=path, **details)

    def deliverable_status(self) -> list[dict]:
        return [
            {"id": ref, **item.model_dump(), "artifact": self.fulfilled.get(ref)}
            for ref, item in self.deliverables.items()
        ]

    @contextmanager
    def verify(self, step: StepTrace, kind: str, **details: object) -> Iterator[dict]:
        started = monotonic()
        request_start = len(self.budget.requests)
        result = {"kind": kind, "passed": False, "step": step.number, **details}
        try:
            yield result
        except (BudgetExhausted, PlannerError, ToolError, OSError) as exc:
            result["explanation"] = (
                "Could not access a local file during verification." if isinstance(exc, OSError) else str(exc)
            )
            raise
        finally:
            elapsed = monotonic() - started
            step.verification_seconds += elapsed
            step.verification_requests.update(range(request_start, len(self.budget.requests)))
            self.verification.append(result)
            self.record("verification", **result, latency_seconds=round(elapsed, 3),
                        **_request_summary(self.budget.requests[request_start:]))


def _review_translation(run: Run, planner: Planner, step: StepTrace,
                        source: str, language: Language, original: str, result: TranslationResult,
                        **details: object) -> None:
    if not result.segments:
        with run.verify(step, "translation_coverage", method="literal_preservation",
                        source=source, language=language, **details) as check:
            check["passed"] = original == result.translation
            if not check["passed"]:
                raise ToolError("Untranslated literal text changed.")
        return

    first = 0
    while first < len(result.segments):
        start = result.segments[first]
        last = first
        while last + 1 < len(result.segments):
            following = result.segments[last + 1]
            source_size = len(original[start.source_start:following.source_end].encode("utf-8"))
            if source_size > REVIEW_BYTES:
                break
            last += 1
        end = result.segments[last]
        source_start, source_end = start.source_start, end.source_end
        output_start, output_end = start.output_start, end.output_end
        # Include literal headers, numeric cells and closing markup for a short document.
        if first == 0 and last == len(result.segments) - 1 and len(original.encode("utf-8")) <= REVIEW_BYTES:
            source_start, source_end = 0, len(original)
            output_start, output_end = 0, len(result.translation)
        with run.verify(step, "translation_coverage", method="planner_review",
                        source=source, language=language, **details,
                        segments=[first + 1, last + 1],
                        source_range=[source_start, source_end]) as check:
            review = planner.review(original[source_start:source_end],
                                    result.translation[output_start:output_end], language)
            check.update(passed=review.approved, explanation=review.explanation)
        if not review.approved:
            raise ToolError(f"Translation segments {first + 1}–{last + 1} did not pass review.")
        first = last + 1


def _read_ocr(run: Run, source_id: str, source: Path) -> dict:
    pages: list[OcrPage] = []
    try:
        read_document(str(source), on_page=pages.append)
    except (BudgetExhausted, ToolError):
        if pages:
            path = f"artifacts/partial-{source_id}.md"
            text = "\n\n".join(f"## Page {page.page_number}\n\n{page.markdown}" for page in pages)
            run.save_partial(path, text, input=source_id, pages=[page.page_number for page in pages])
        raise

    text = []
    ranges = []
    offset = 0
    for page in pages:
        text.append(page.markdown)
        ranges.append({"page": page.page_number, "start": offset, "end": offset + len(page.markdown)})
        offset += len(page.markdown) + 2
    return {**run.remember("\n\n".join(text)), "page_ranges": ranges}


def _non_latin_letters(text: str) -> bool:
    return any(char.isalpha() and "LATIN" not in unicodedata.name(char, "") for char in text)


def _summarize(run: Run, planner: Planner, step: StepTrace, action: Summarize, source: str) -> dict:
    if run.text_languages.get(action.source) not in {None, "en"} or _non_latin_letters(source):
        raise ToolError("Translate the source into English with Indic-Translate before summarizing.")
    durations = {item.duration_seconds for item in run.deliverables.values() if item.duration_seconds}
    target_words = action.target_words
    word_limit = None
    if len(durations) == 1:
        seconds = durations.pop()
        suggested = max(1, seconds * SUMMARY_WORDS_PER_MINUTE // 60)
        word_limit = max(10, suggested * 3 // 2)
        target_words = min(target_words or suggested, word_limit)
    summary = planner.summarize(source, run.task, target_words)
    if not summary or not any(char.isalpha() for char in summary) or _non_latin_letters(summary):
        raise ToolError("The planner did not return an English summary.")
    word_count = len(summary.split())
    if word_limit is not None and word_count > word_limit:
        raise ToolError(f"The English narration has {word_count} words. Revise it to fit {word_limit} words.")
    with run.verify(step, "summary_grounding", method="planner_review", source=action.source,
                    words=word_count, target_words=target_words) as check:
        review = planner.review_summary(source, summary)
        check.update(passed=review.approved, explanation=review.explanation)
    if not review.approved:
        raise ToolError("The English summary did not pass grounding review: " + review.explanation)
    return {**run.remember(summary, "en"), "words": word_count, "target_words": target_words}


def _speak(run: Run, step: StepTrace, action: Speak, text: str) -> dict:
    if len(text.encode("utf-8")) > MAX_SPEECH_BYTES:
        raise ToolError("Text exceeds this increment's speech input limit.")
    known_language = run.text_languages.get(action.source)
    if known_language is not None and known_language != action.language:
        raise ToolError("Translate the source into the speech language before synthesis.")
    path = run.target(action.deliverable, {"wav", "mp3"}, action.language)
    if path in run.artifacts:
        destination = Path(path)
        path = str(destination.with_stem(f"{destination.stem}-attempt-{step.number}"))
    target = run.deliverables[action.deliverable].duration_seconds
    synthesis = synthesize_speech(text, action.language, action.voice, str(run.output / path),
                                  target_seconds=target)
    with run.verify(step, "speech_artifact_created", artifact=path, ai_generated=True) as check:
        run.artifacts.append(path)
        check["passed"] = True
    try:
        with run.verify(step, "speech_content_match", method="back_transcription",
                        source=action.source, artifact=path, language=action.language) as check:
            readback = transcribe_audio(synthesis.audio_path, action.language)
            expected = _normalized(text)
            actual = _normalized(readback.text)
            similarity = SequenceMatcher(None, expected, actual, autojunk=False).ratio()
            passed = bool(expected and actual and similarity >= 0.85)
            check.update(passed=passed, similarity=round(similarity, 3), threshold=0.85,
                         transcript_characters=len(readback.text))
        if not passed:
            raise ToolError(f"Speech read-back diverged from the source. See {path} for review.")
        duration = round(readback.duration_seconds, 3)
        if target is not None:
            tolerance = max(1.0, target * 0.15)
            with run.verify(step, "speech_duration", artifact=path, target_seconds=target) as check:
                passed = abs(duration - target) <= tolerance
                check.update(passed=passed, actual_seconds=duration, tolerance_seconds=tolerance)
                if not passed:
                    raise ToolError(
                        f"Audio runs {duration:g} seconds against a {target}-second target. "
                        f"Use a revised narration or review the audio at {path}."
                    )
    except (BudgetExhausted, OSError, ToolError):
        run.partial_artifacts.append(path)
        raise
    run.fulfilled[action.deliverable] = path
    return {"artifact": path, "deliverable": action.deliverable,
            "duration_seconds": duration, "ai_generated": True}


def _save_subtitles(run: Run, planner: Planner, step: StepTrace, action: SaveSubtitles) -> dict:
    transcript = run.transcriptions.get(action.source)
    if transcript is None:
        raise ToolError("Select the text ID returned by transcribing this recording.")
    requirement = run.deliverables.get(action.deliverable)
    source_language = run.text_languages.get(action.source)
    target_language = requirement.language if requirement else None
    path = run.target(action.deliverable, {"srt"}, target_language or source_language)
    needs_translation = target_language is not None and target_language != source_language
    if needs_translation and target_language not in get_args(Language):
        raise ToolError(f"Bodhan does not support subtitle translation into {target_language}.")

    captions = {index: window.text.strip() for index, window in enumerate(transcript.windows)
                if window.text.strip()}
    if needs_translation:
        language = cast(Language, target_language)
        pending = {}
        for index, text in captions.items():
            if (text, language) not in run.subtitle_translations:
                pending.setdefault(text, index + 1)
        for group in batched(pending.items(), SUBTITLE_REVIEW_CUES):
            originals = {index: text for text, index in group}
            translated = {index: translate_text(text, language).translation for index, text in originals.items()}
            with run.verify(step, "translation_coverage", method="subtitle_group_review", source=action.source,
                            language=language, windows=list(originals)) as check:
                review = planner.review(json.dumps(originals, ensure_ascii=False),
                                        json.dumps(translated, ensure_ascii=False), language)
                check.update(passed=review.approved, explanation=review.explanation)
                if not review.approved:
                    window_labels = ", ".join(map(str, originals))
                    raise ToolError(f"Subtitle translation review failed for windows {window_labels}: "
                                    + review.explanation)
            for index, text in originals.items():
                run.subtitle_translations[(text, language)] = translated[index]
        captions = {index: run.subtitle_translations[(text, language)] for index, text in captions.items()}

    with run.verify(step, "subtitle_cues", method="window_coverage", source=action.source,
                    cues=len(captions), timing="audio_window_boundaries",
                    duration_seconds=transcript.duration_seconds) as check:
        srt = render_srt(transcript, captions)
        check["passed"] = True
    (run.output / path).write_text(srt, encoding="utf-8")
    run.artifacts.append(path)
    run.fulfilled[action.deliverable] = path
    return {"artifact": path, "deliverable": action.deliverable, "cue_count": len(captions),
            "duration_seconds": transcript.duration_seconds, "timing": "audio_window_boundaries"}


def _render_clip_selection(selection: dict, translations: dict[str, str], markdown: bool) -> str:
    prefix = "#" if markdown else ""
    heading = f"Selected language: {selection['source_language']}"
    sections = [f"{prefix} {heading}".strip()]
    sections.append("Selection uses script evidence from automatic transcription.")
    if not translations:
        sections.append("No verified translations are available.")
    for clip in selection["clips"]:
        name = " ".join(clip["name"].splitlines())
        if markdown:
            name = "".join("\\" + char if char in "\\`*_{}[]<>#!|&" else char for char in name)
        heading = f"{clip['input']} — {name}"
        sections.append(f"{prefix * 2} {heading}".strip())
        translation = translations.get(clip["input"])
        if translation is not None:
            sections.append(f"Translation ({selection['target_language']}): " + translation)
        elif clip.get("translation") == "pending":
            sections.append("The script points to the requested language. Translation is pending.")
        else:
            sections.append(clip["explanation"])
    return "\n\n".join(sections) + "\n"


def _translate_matching_clips(
    run: Run, planner: Planner, inputs: dict[str, Path], action: TranslateMatchingClips, step: StepTrace,
) -> dict:
    path = run.target(action.deliverable, {"md", "txt"}, action.target_language)
    candidates = {ref: file for ref, file in inputs.items() if file.suffix.lower() in AUDIO_SUFFIXES}
    if not candidates:
        raise ToolError("No supported audio files were found in the input inventory.")
    clips = [
        {"input": ref, "name": file.name, "status": "pending", "scripts": [],
         "explanation": "The clip has not been assessed."}
        for ref, file in candidates.items()
    ]
    selection = {
        "source_language": action.source_language, "target_language": action.target_language,
        "method": "autodetected_transcription_script", "clips": clips,
    }
    run.clip_selections[action.deliverable] = selection
    translations: dict[str, str] = {}
    markdown = path.endswith(".md")
    try:
        for clip in clips:
            ref = clip["input"]
            try:
                transcript = transcribe_audio(str(candidates[ref]))
                status, scripts, explanation = assess_transcript(transcript.text, action.source_language)
                clip.update(status=status, scripts=scripts, explanation=explanation)
                if status != "match":
                    continue
                clip["translation"] = "pending"
                result = translate_text(transcript.text, action.target_language)
                _review_translation(run, planner, step, ref, action.target_language, transcript.text, result)
                translations[ref] = result.translation
                clip["translation"] = "verified"
            except (ToolError, PlannerError, OSError) as exc:
                if clip["status"] == "match":
                    clip["translation"] = "failed"
                else:
                    clip["status"] = "assessment_failed"
                clip["explanation"] = str(exc) if not isinstance(exc, OSError) else "Could not read the audio."
        if any(clip["status"] == "assessment_failed" or clip.get("translation") == "failed" for clip in clips):
            raise ToolError("One or more clips could not be assessed or translated. See the partial clip result.")
    except (BudgetExhausted, ToolError):
        partial = f"artifacts/partial-{action.deliverable}-{step.number}.{Path(path).suffix[1:]}"
        run.save_partial(partial, _render_clip_selection(selection, translations, markdown),
                         deliverable=action.deliverable)
        raise

    (run.output / path).write_text(_render_clip_selection(selection, translations, markdown), encoding="utf-8")
    run.artifacts.append(path)
    run.fulfilled[action.deliverable] = path
    return {"artifact": path, "deliverable": action.deliverable, **selection}


def _execute(run: Run, planner: Planner, inputs: dict[str, Path], action: Action, step: StepTrace) -> dict:
    if isinstance(action, (Read, Transcribe)):
        source = inputs.get(action.source)
        if source is None:
            raise ToolError("Select an input ID from the inventory.")
        if isinstance(action, Transcribe):
            if any(item.format == "srt" for item in run.deliverables.values()):
                transcript = transcribe_audio(str(source), action.language, SUBTITLE_WINDOW_SECONDS, prefer_pauses=True)
            else:
                transcript = transcribe_audio(str(source), action.language)
            observation = run.remember(transcript.text, action.language)
            run.transcriptions[observation["text_id"]] = transcript
            return {**observation, "windows": len(transcript.windows),
                    "duration_seconds": transcript.duration_seconds}
        if action.action == "ocr":
            return _read_ocr(run, action.source, source)
        if source.suffix.lower() not in {".txt", ".md"}:
            raise ToolError("Read supports UTF-8 .txt and .md files. Use OCR for documents.")
        with source.open("rb") as file:
            data = file.read(MAX_SOURCE_BYTES + 1)
        if len(data) > MAX_SOURCE_BYTES:
            raise ToolError("Text exceeds this increment's input size limit.")
        return run.remember(data.decode("utf-8"))

    if isinstance(action, TranslateMatchingClips):
        return _translate_matching_clips(run, planner, inputs, action, step)

    if isinstance(action, SaveSubtitles):
        return _save_subtitles(run, planner, step, action)

    text = run.texts.get(action.source)
    if text is None:
        raise ToolError("Select an available text ID.")
    if isinstance(action, Save):
        path = run.target(action.deliverable, {"md", "txt"}, run.text_languages.get(action.source))
        exported = markdown_for_export(text) if path.endswith(".md") else text
        (run.output / path).write_text(exported, encoding="utf-8")
        run.artifacts.append(path)
        run.fulfilled[action.deliverable] = path
        return {"artifact": path, "deliverable": action.deliverable}
    if isinstance(action, Translate):
        result = translate_text(text, action.language)
        try:
            _review_translation(run, planner, step, action.source, action.language, text, result)
        except (PlannerError, ToolError):
            path = f"artifacts/unverified-{step.number}.md"
            run.save_partial(path, result.translation, source=action.source,
                             language=action.language, verification="unverified")
            raise
        return {**run.remember(result.translation, action.language), "segments": len(result.segments)}

    if isinstance(action, Summarize):
        return _summarize(run, planner, step, action, text)
    return _speak(run, step, action, text)


def run_task(task: str, files: list[Path], output: Path, max_steps: int, max_cost: Decimal,
             make_planner: Callable[[], Planner]) -> dict:
    started = monotonic()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Choose a new or empty output directory.")
    (output / "artifacts").mkdir()
    run = Run(output, RequestBudget(max_cost), task)
    run.record("start", task=task, max_steps=max_steps, max_cost_inr=max_cost)
    token = active_budget.set(run.budget)
    status = "step_limit"
    explanation = "The agent reached its maximum number of steps."
    assumptions: list[str] = []
    step = 0
    seen: set[str] = set()
    try:
        inputs = _inputs(files)
        if not inputs:
            raise ToolError("No input files were found.")
        planner = make_planner()
        for step in range(1, max_steps + 1):
            step_start = monotonic()
            action = planner.decide({
                "task": task,
                "inputs": [{"id": ref, "name": path.name} for ref, path in inputs.items()],
                "observations": run.history,
                "deliverables": run.deliverable_status(),
                "remaining_bodhan_inr": str(max_cost - run.budget.total),
                "remaining_steps_after_this": max_steps - step,
            })
            arguments = action.model_dump(exclude={"reason"})
            run.record("plan", step=step, reason=action.reason, arguments=arguments,
                       latency_seconds=round(monotonic() - step_start, 3))
            if isinstance(action, Finish):
                status, explanation = action.status, action.reason
                assumptions = list(dict.fromkeys([*assumptions, *action.assumptions]))
                if status == "completed" and (
                    not run.deliverables or run.deliverables.keys() != run.fulfilled.keys()
                ):
                    status = "incomplete"
                    explanation = "One or more requested deliverables are missing or unverified."
                break

            if isinstance(action, Declare):
                if step != 1:
                    raise PlannerError("The planner attempted to change the original deliverable declaration.")
                assumptions = list(action.assumptions)
                run.deliverables = {f"deliverable-{i}": item
                                    for i, item in enumerate(action.deliverables, start=1)}
                unsupported = [item.format for item in action.deliverables
                               if item.format not in {"md", "txt", "wav", "mp3", "srt"}]
                if unsupported:
                    status = "unsupported"
                    explanation = "Unsupported output format: " + ", ".join(dict.fromkeys(unsupported))
                    break
                supported_text = get_args(Language) + get_args(TranscriptionLanguage)
                for item in run.deliverables.values():
                    languages = get_args(SpeechLanguage) if item.format in {"mp3", "wav"} else supported_text
                    if item.language is not None and item.language not in languages:
                        status = "unsupported"
                        explanation = f"Bodhan does not support the requested output language ({item.language})."
                        break
                if status == "unsupported":
                    break
                continue
            if not run.deliverables:
                raise PlannerError("The planner must declare the requested deliverables before using tools.")

            request_index = len(run.budget.requests)
            tool_start = monotonic()
            timing = StepTrace(step)
            signature = json.dumps(arguments, sort_keys=True)
            observation = {"error": "The action did not finish."}
            try:
                if signature in seen:
                    raise ToolError("This action was already attempted. Reuse its observation.")
                seen.add(signature)
                observation = _execute(run, planner, inputs, action, timing)
            except BudgetExhausted as exc:
                observation = {"error": str(exc)}
                raise
            except (ToolError, UnicodeError, OSError, PlannerError) as exc:
                observation = {"error": str(exc) if isinstance(exc, (ToolError, PlannerError))
                               else "Could not read or write a local file."}
            finally:
                requests = [
                    request for index, request in enumerate(run.budget.requests[request_index:], request_index)
                    if index not in timing.verification_requests
                ]
                elapsed = monotonic() - tool_start - timing.verification_seconds
                run.record("tool", step=step, arguments=arguments, observation=observation,
                           latency_seconds=round(elapsed, 3), **_request_summary(requests))
            run.history.append({"step": step, "action": arguments, "result": observation})
    except BudgetExhausted as exc:
        status, explanation = "budget_exhausted", str(exc)
    except (ToolError, PlannerError, OSError) as exc:
        status = "failed"
        explanation = str(exc) if not isinstance(exc, OSError) else "Could not read a local input."
    finally:
        active_budget.reset(token)

    if any(path.endswith(".srt") for path in run.fulfilled.values()):
        assumptions = list(dict.fromkeys([*assumptions, SUBTITLE_TIMING_NOTE]))
    report = {
        "status": status, "steps": step, "verification": run.verification,
        "cost_inr": str(run.budget.total), "max_cost_inr": str(max_cost),
        "cost_scope": "Bodhan requests only. Unknown charges use the reserved amount.",
        "elapsed_seconds": round(monotonic() - started, 3),
        "artifacts": run.artifacts, "partial_artifacts": run.partial_artifacts,
        "deliverables": run.deliverable_status(), "assumptions": assumptions, "explanation": explanation,
    }
    if run.clip_selections:
        report["clip_selections"] = run.clip_selections
    run.record("summary", **report)
    (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    links = "\n".join(
        f"- [{Path(path).name}]({path})"
        + (" (AI-generated audio)" if Path(path).suffix in {".wav", ".mp3"} else "")
        + (" (pending verification or incomplete)" if path in run.partial_artifacts else "")
        for path in run.artifacts
    ) or "No artifacts were produced."
    deliverable_lines = "\n".join(
        f"- {item['description']} (`{item['format']}`) — " +
        (f"[ready]({item['artifact']})" if item["artifact"] else "pending")
        for item in report["deliverables"]
    ) or "None declared."
    assumptions_text = "\n".join(f"- {item}" for item in assumptions) or "None recorded."
    markdown = (
        f"# Task result\n\n**{status}**\n\n{explanation}\n\n"
        f"Bodhan cost estimate: ₹{report['cost_inr']}\n\n"
        f"## Requested deliverables\n\n{deliverable_lines}\n\n## Artifacts\n\n{links}\n\n## Assumptions\n\n{assumptions_text}\n\n"
        "See [report.json](report.json) for verification and [trace.jsonl](trace.jsonl) for the execution trace.\n"
    )
    (output / "result.md").write_text(markdown, encoding="utf-8")
    return report
