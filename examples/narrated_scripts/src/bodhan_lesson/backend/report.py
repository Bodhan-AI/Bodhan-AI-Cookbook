"""Run-scoped attempts and an audit view of typed checkpoint results."""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from math import fsum

from .audio.export import LUFS_TOLERANCE, TARGET_LUFS
from .models import (
    Attempt, AudioRun, LanguageDecision, NarrationScripts, OCRPage, Page, PreprocessedPage,
    SourceChapter, SpeechQARun, SpeechRun, TargetAudio, TranslationPlan, TranslationQARun, TranslationRun, Usage,
)

STRUCTURAL_KINDS = {"heading", "table", "equation"}
HOSTED_RUNTIME_LIMIT_SECONDS = 20 * 60


@dataclass
class AttemptLog:
    attempts: list[Attempt] = field(default_factory=list)
    on_record: Callable[[Attempt], None] | None = None

    def record(self, **fields) -> Attempt:
        attempt = Attempt(id=f"a{len(self.attempts) + 1:06d}", **fields)
        self.attempts.append(attempt)
        if self.on_record is not None:
            self.on_record(attempt)
        return attempt


def _usage_summary(records: list[Usage]) -> dict:
    amounts = [usage.quantity for usage in records if usage.quantity is not None]
    charges = [usage.cost_inr for usage in records if usage.cost_inr is not None]
    unknown = len(records) - len(charges)
    return {
        "quantity": fsum(amounts) if len(amounts) == len(records) else None,
        "known_quantity": fsum(amounts),
        "cost_inr": None if unknown else fsum(charges),
        "known_cost_inr": fsum(charges),
        "unknown_cost_attempts": unknown,
        "basis": ("unknown" if unknown else
                  "estimated" if any(usage.basis == "estimated" for usage in records) else "measured"),
        "reasons": sorted({usage.reason for usage in records if usage.reason}),
    }


def summarize_cost(attempts: list[Attempt]) -> dict:
    """Count each attempt ID once, including shared work and every transport retry."""
    unique = {attempt.id: attempt for attempt in attempts}.values()
    groups = defaultdict(list)
    for attempt in unique:
        usage = attempt.usage
        groups[(attempt.service, attempt.backend, usage.unit, usage.rate_inr)].append(usage)
    return {
        "currency": "INR",
        "hosted": _usage_summary([attempt.usage for attempt in unique if attempt.backend == "hosted"])
        | {"quantity": None, "known_quantity": None},  # Hosted services use different units.
        "by_service": [
            {"service": service, "backend": backend, "unit": unit, "rate_inr": rate,
             "attempt_count": len(usages), **_usage_summary(usages)}
            for (service, backend, unit, rate), usages in groups.items()
        ],
    }


def audio_run_status(audio: AudioRun) -> str:
    if audio.failed_languages:
        return "partial" if len(audio.failed_languages) < len(audio.targets) else "failed"
    if any(target.status == "completed_with_misses" for target in audio.targets):
        return "completed_with_misses"
    return "completed"


class RunReport:
    def __init__(self, pages: tuple[Page, ...], languages: tuple[str, ...], mode: str, attempts: AttemptLog):
        self.pages = {page.page_id: {"page_id": page.page_id, "input_index": page.input_index,
                                    "status": "pending", "reason": None} for page in pages}
        self.languages = {language: {"language": language, "status": "pending", "reason": None,
                                     "sections": {}} for language in languages}
        self.mode = mode
        self.attempts = attempts
        self.stages: dict[str, dict] = {}
        self.current_stage = "setup"
        self.attempt_stages: dict[str, str] = {}
        self.live_attempt_ids: set[str] = set()
        self.source_language: dict | None = None
        self.source_quality: dict = {}
        self.structural_ocr_ids: set[str] = set()
        self.source_sections: dict[str, tuple[str, ...]] = {}
        self.unit_sections: dict[str, str] = {}
        self.status = "running"
        self.failure: dict | None = None

    def record_stage(self, stage: str, result, elapsed_seconds: float, reused: bool) -> None:
        self.stages[stage] = {"status": "completed", "elapsed_seconds": elapsed_seconds, "reused": reused}
        if isinstance(result, LanguageDecision):
            self.source_language = {
                "code": result.code, "method": result.method, "reason": result.reason,
                "artifact": "source/language.json", "attempt_ids": [attempt.id for attempt in result.attempts],
            }
        elif stage == "preprocess":
            for page in result:
                self.record_preprocess(page)
        elif stage in ("ocr", "layout"):
            for page in result:
                self.record_page(page)
        elif isinstance(result, SourceChapter):
            self.record_source(result)
        elif isinstance(result, TranslationPlan):
            self.unit_sections = {unit.id: unit.section_id for unit in result.units}
        elif isinstance(result, TranslationRun):
            self.record_segments(result)
        elif isinstance(result, TranslationQARun):
            self.record_translation(result)
        elif isinstance(result, NarrationScripts):
            self.record_scripts(result)
        elif isinstance(result, SpeechRun):
            for target in result.targets:
                self.languages[target.language]["speech"] = {
                    "status": target.status, "reason": target.reason, "artifact": f"{target.language}/chunks.json",
                    "failed_chunks": [{"id": chunk.chunk_id, "reason": chunk.reason,
                                       "attempt_ids": chunk.attempt_ids}
                                      for chunk in target.chunks if chunk.status == "failed"],
                }
        elif isinstance(result, SpeechQARun):
            self.record_speech_qa(result)
        elif isinstance(result, AudioRun):
            for target in result.targets:
                self.record_audio(target)
            self.status = audio_run_status(result)
            source_misses = any(result["status"] == "missed" for result in self.source_quality.values())
            if self.status == "completed" and (source_misses or any(
                page["status"] != "ok" for page in self.pages.values()
            )):
                self.status = "completed_with_misses"

    def record_preprocess(self, page: PreprocessedPage) -> None:
        self.pages[page.page_id].update(
            status="pending" if page.status == "ok" else "failed", reason=page.reason,
            preprocess={"status": page.status, "reason": page.reason,
                        "artifact": f"work/images/{page.page_id}.transform.json"},
        )

    def record_page(self, page: OCRPage) -> None:
        self.structural_ocr_ids.update(block.id for block in page.blocks if block.kind in STRUCTURAL_KINDS)
        self.pages[page.page_id].update(
            status=page.status, reason=page.reason, flags=page.flags, ordered_block_ids=page.ordered_block_ids,
            artifact=f"source/pages/{page.page_id}.json",
            quarantined_block_ids=[block.id for block in page.blocks if "quarantined" in block.flags],
        )

    def record_source(self, chapter: SourceChapter) -> None:
        captured = set()
        excluded = set()
        for section in chapter.sections:
            ids = tuple(dict.fromkeys(block_id for block in section.blocks for block_id in block.source_block_ids))
            self.source_sections[section.id] = ids
            for language in self.languages:
                self._section(language, section.id)
            for block in section.blocks:
                if block.eligible:
                    if block.kind in STRUCTURAL_KINDS:
                        captured.update(block.source_block_ids)
                else:
                    excluded.update(block.source_block_ids)
        missing = sorted(self.structural_ocr_ids - captured)
        failed_pages = [page_id for page_id, page in self.pages.items() if page["status"] == "failed"]
        quarantined = {block_id for page in self.pages.values() for block_id in page.get("quarantined_block_ids", ())}
        self.source_quality = {
            "structure_capture": {
                "status": "missed" if missing or failed_pages else "met",
                "observed_ocr_blocks": len(self.structural_ocr_ids),
                "captured_ocr_blocks": len(self.structural_ocr_ids & captured),
                "missing_block_ids": missing, "failed_page_ids": failed_pages,
                "reason": ("Some observed structure was excluded or some pages could not be read."
                           if missing or failed_pages else None),
                "scope": "OCR-detected structure only. Scan-level completeness and reading order require review.",
                "artifact": "source/lesson.src.md",
            },
            "doubtful_ocr": {
                "status": "missed" if quarantined - excluded else "met",
                "quarantined_block_ids": sorted(quarantined),
                "unaccounted_block_ids": sorted(quarantined - excluded),
                "reason": ("Some quarantined IDs have no source gap." if quarantined - excluded else None),
                "artifact": "work/layout.json",
            },
        }

    def _section(self, language: str, section_id: str) -> dict:
        return self.languages[language]["sections"].setdefault(
            section_id, {"section_id": section_id, "source_block_ids": self.source_sections.get(section_id, ())},
        )

    def record_segments(self, run: TranslationRun) -> None:
        for target in run.targets:
            self.languages[target.target_lang]["segments"] = {
                "status": target.status, "reason": target.reason,
                "artifact": f"{target.target_lang}/segments.json",
                "shared_pivot_artifact": "work/pivot.en.json" if run.pivots else None,
            }
            for unit in target.units:
                section_id = self.unit_sections.get(unit.unit_id)
                if section_id is not None:
                    section = self._section(target.target_lang, section_id)
                    section.setdefault("translation_attempt_ids", []).extend(
                        attempt_id for leg in unit.legs for attempt_id in leg.attempt_ids
                    )

    def record_translation(self, run: TranslationQARun) -> None:
        for lesson in run.lessons:
            qa = lesson.qa
            language = self.languages[qa.target_lang]
            language["translation"] = {
                "status": qa.status, "reason": qa.reason, "artifact": f"{qa.target_lang}/translation-qa.json",
                "markdown": f"{qa.target_lang}/lesson.{qa.target_lang}.md", "issues": [asdict(i) for i in qa.issues],
            }
            for section in qa.sections:
                units = section.units
                structural = [unit for unit in units if unit.address.kind in STRUCTURAL_KINDS | {"table_cell"}]
                issues = [unit for unit in units if unit.issues or unit.retry_issues or not unit.eligible]
                unchecked = any(not unit.eligible for unit in units)
                literal_miss = any(issue.check in {"literal", "literals", "placeholders"}
                                   for unit in units if not unit.eligible
                                   for issue in (*unit.issues, *unit.retry_issues))
                self._section(qa.target_lang, section.section_id)["translation"] = {
                    "status": section.status, "reason": section.reason,
                    "unavailable_source_ids": section.unavailable_source_ids,
                    "structure_survival": {
                        "status": "missed" if any(not unit.eligible for unit in structural) else "met",
                        "accepted_units": sum(unit.eligible for unit in structural), "total_units": len(structural),
                        "reason": ("Some structural units have no accepted translation."
                                   if any(not unit.eligible for unit in structural) else None),
                    },
                    "numbers_and_latex": {
                        "status": "missed" if literal_miss else "unverified" if unchecked else "met",
                        "checked_units": sum(unit.eligible for unit in units), "total_units": len(units),
                        "reason": ("Some units lack an accepted structure check."
                                   if unchecked else None),
                    },
                    "unit_findings": [
                        {"unit_id": unit.unit_id, "source_block_ids": unit.source_block_ids,
                         "disposition": unit.disposition, "attempt_ids": unit.retry_attempt_ids,
                         "issues": [asdict(issue) for issue in (*unit.issues, *unit.retry_issues)]}
                        for unit in issues
                    ],
                }

    def record_scripts(self, run: NarrationScripts) -> None:
        for script in run.scripts:
            for section in script.sections:
                report = self._section(script.language, section.section_id)
                report["script"] = {
                    "artifact": f"{script.language}/narration_script.{script.language}.json",
                    "reason": section.reason, "issues": [asdict(issue) for issue in section.issues],
                    "chunks": [{"id": chunk.id, "source_block_ids": chunk.source_block_ids,
                                "unit_ids": chunk.unit_ids, "attempt_ids": chunk.translation_attempt_ids}
                               for chunk in section.chunks],
                }

    def record_speech_qa(self, run: SpeechQARun) -> None:
        for target in run.targets:
            artifact = f"{target.language}/speech-qa.json"
            self.languages[target.language]["speech_qa"] = {
                "status": target.status, "reason": target.reason, "cer_threshold": target.cer_threshold,
                "artifact": artifact, "metrics": asdict(target.metrics),
            }
            for section in target.sections:
                metrics = section.metrics
                self._section(target.language, section.section_id)["content_fidelity"] = {
                    "status": ("unverified" if metrics.cer is None else
                               "missed" if metrics.cer > target.cer_threshold or metrics.coverage != 1 else "met"),
                    "metrics": asdict(metrics), "reason": section.reason or metrics.reason,
                    "cer_threshold": target.cer_threshold, "artifact": artifact,
                    "chunks": [{"chunk_id": chunk.chunk_id, "status": chunk.status, "reason": chunk.reason,
                                "accepted_candidate_ids": chunk.accepted_candidate_ids,
                                "attempt_ids": list(dict.fromkeys(
                                    attempt_id for review in chunk.attempts
                                    for attempt_id in (*review.candidate.attempt_ids, *review.transcribe_attempt_ids)
                                )),
                                "reviews": [
                                    {"phase": review.phase, "candidate_id": review.candidate.attempt_id,
                                     "outcome": review.outcome, "cer": review.comparison.cer,
                                     "edits": review.comparison.total_edits,
                                     "reason": review.reason, "signals": review.comparison.signals}
                                    for review in chunk.attempts
                                ]} for chunk in section.chunks],
                }

    def record_audio(self, target: TargetAudio) -> None:
        language = self.languages[target.language]
        language.update(
            status=target.status, reason=target.reason, lesson_audio_path=target.audio_path,
            duration_seconds=target.duration_seconds, chapters=[asdict(marker) for marker in target.chapters],
            audio_coverage=target.coverage,
            section_coverage={"narrated": sum(bool(section.audio_path) for section in target.sections),
                              "total": len(language["sections"]) or len(target.sections)},
        )
        for section in target.sections:
            self._section(target.language, section.section_id).update(
                narration_coverage={"status": section.coverage_status,
                                    "reason": section.reason, "audio_path": section.audio_path,
                                    "duration_seconds": section.duration_seconds,
                                    "omitted_chunk_ids": list(section.omitted_chunk_ids)},
                loudness={"status": section.loudness_status, "measured_lufs": section.loudness_lufs,
                          "target_lufs": TARGET_LUFS, "tolerance_lufs": LUFS_TOLERANCE,
                          "reason": section.loudness_reason or (section.reason if not section.audio_path else None)},
            )
        for section in language["sections"].values():
            section.setdefault("narration_coverage", {
                "status": "skipped", "reason": target.reason or "No section export was recorded.",
                "audio_path": None, "duration_seconds": None,
            })

    def fail(self, category: str, reason: str, *, interrupted: bool = False) -> None:
        has_completed = any(language["status"] in {"completed", "completed_with_misses"}
                            for language in self.languages.values())
        self.status = "interrupted" if interrupted else "partial" if has_completed else "failed"
        self.failure = {"stage": self.current_stage, "category": category, "reason": reason}
        self.stages[self.current_stage] = {"status": "interrupted" if interrupted else "failed", "reason": reason}
        if not interrupted:
            for language in self.languages.values():
                if language["status"] == "pending":
                    language.update(status="failed", reason=reason)
                    for section in language["sections"].values():
                        section["narration_coverage"] = {
                            "status": "skipped", "reason": reason, "audio_path": None, "duration_seconds": None,
                        }

    def snapshot(self, wall_seconds: float) -> dict:
        attempts = list({attempt.id: attempt for attempt in self.attempts.attempts}.values())
        live = [attempt for attempt in attempts if attempt.id in self.live_attempt_ids]
        hosted = [attempt for attempt in attempts if attempt.backend == "hosted"]
        benchmark = (
            self.mode == "hosted" and len(self.pages) == 10 and len(self.languages) == 3
            and self.status in {"completed", "completed_with_misses"}
            and all(attempt.id in self.live_attempt_ids for attempt in attempts)
            and not any(stage["reused"] for stage in self.stages.values())
        )
        return {
            "status": self.status, "failure": self.failure, "wall_time_seconds": wall_seconds,
            "stages": self.stages, "source_language": self.source_language,
            "pages": [
                {**page, "attempt_ids": [attempt.id for attempt in attempts if any(
                    entity.split("-", 1)[0] == page_id and attempt.service in {"indic-ocr", "llm"}
                    for entity in attempt.entity_ids
                )]} for page_id, page in self.pages.items()
            ],
            "languages": [{**language, "sections": list(language["sections"].values())}
                          for language in self.languages.values()],
            "attempts": [{**asdict(attempt), "stage": self.attempt_stages.get(attempt.id),
                          "reused": attempt.id not in self.live_attempt_ids} for attempt in attempts],
            "attempt_elapsed_ms": sum(attempt.elapsed_ms for attempt in attempts),
            "invocation_attempt_elapsed_ms": sum(attempt.elapsed_ms for attempt in live),
            "cost": summarize_cost(attempts), "invocation_cost": summarize_cost(live),
            "backend": {
                "requested_mode": self.mode, "local_attempts": sum(attempt.backend == "local" for attempt in attempts),
                "hosted_indic_attempts": sum(attempt.service.startswith("indic-") for attempt in hosted),
                "local_cuda_oom_attempts": sum(attempt.error_category == "cuda_oom" for attempt in attempts),
                "hosted_fallback_attempts": sum(bool(attempt.fallback_reason) for attempt in hosted),
            },
            "quality_targets": {**self.source_quality, "hosted_runtime": {
                "status": ("met" if wall_seconds < HOSTED_RUNTIME_LIMIT_SECONDS else "missed")
                if benchmark else "unverified",
                "limit_seconds": HOSTED_RUNTIME_LIMIT_SECONDS, "measured_seconds": wall_seconds if benchmark else None,
                "reason": ("Exceeds the hosted runtime target." if wall_seconds >= HOSTED_RUNTIME_LIMIT_SECONDS else None)
                if benchmark else "Requires a completed, fresh hosted run on 10 pages and three target languages.",
            }},
        }
