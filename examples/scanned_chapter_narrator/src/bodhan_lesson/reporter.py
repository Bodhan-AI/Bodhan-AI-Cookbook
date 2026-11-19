"""Comprehensive report generator and financial cost auditing for Bodhan Lesson runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bodhan_lesson.config import (
    PRICE_OCR_PER_IMAGE,
    PRICE_SPEAK_PER_10K_CHARS,
    PRICE_TRANSCRIBE_PER_MINUTE,
    PRICE_TRANSLATE_PER_10K_TOKENS,
)
from bodhan_lesson.schemas import DocumentIR, NarrationItem, QAReportSection


class ReportAuditor:
    """Calculates granular API costs and generates structured JSON run reports."""

    def calculate_cost(
        self,
        num_pages: int,
        translate_chars: int,
        tts_chars: int,
        asr_duration_sec: float,
    ) -> dict[str, Any]:
        """Compute itemized costs in INR (₹) according to official Bodhan rate cards."""
        # OCR: ₹0.20 per image
        cost_ocr = num_pages * PRICE_OCR_PER_IMAGE

        # Translate: ₹0.20 per 10k output tokens (approx 1 token ~ 4 chars for Indic/English)
        est_tokens = max(1, translate_chars // 4)
        cost_translate = (est_tokens / 10000.0) * PRICE_TRANSLATE_PER_10K_TOKENS

        # Speak: ₹6.00 per 10k input characters
        cost_speak = (tts_chars / 10000.0) * PRICE_SPEAK_PER_10K_CHARS

        # Transcribe: ₹0.10 per audio minute
        audio_minutes = asr_duration_sec / 60.0
        cost_transcribe = audio_minutes * PRICE_TRANSCRIBE_PER_MINUTE

        total_cost = cost_ocr + cost_translate + cost_speak + cost_transcribe

        return {
            "currency": "INR",
            "ocr_cost_inr": round(cost_ocr, 4),
            "translate_cost_inr": round(cost_translate, 4),
            "speak_cost_inr": round(cost_speak, 4),
            "transcribe_cost_inr": round(cost_transcribe, 4),
            "total_cost_inr": round(total_cost, 4),
            "usage": {
                "pages_ocr": num_pages,
                "translated_characters": translate_chars,
                "estimated_translate_tokens": est_tokens,
                "tts_characters": tts_chars,
                "transcribed_audio_seconds": round(asr_duration_sec, 2),
            },
        }

    def build_report(
        self,
        doc_ir: DocumentIR,
        target_languages: list[str],
        narration_items: dict[str, list[NarrationItem]],
        qa_reports: dict[str, list[QAReportSection]],
        audio_durations: dict[str, float],
        audio_lufs: dict[str, float],
        execution_time_sec: float,
        output_dir: Path,
    ) -> dict[str, Any]:
        """Compile full run report and write to output_dir/report.json."""
        # Tally metrics across languages
        total_translate_chars = 0
        total_tts_chars = 0
        total_asr_sec = 0.0

        per_language_data: dict[str, Any] = {}

        for lang in target_languages:
            items = narration_items.get(lang, [])
            qa_list = qa_reports.get(lang, [])

            lang_tts_chars = sum(len(it.target_script) for it in items)
            lang_translate_chars = sum(len(it.target_script) for it in items)
            total_tts_chars += lang_tts_chars
            total_translate_chars += lang_translate_chars

            duration = audio_durations.get(lang, 0.0)
            lufs = audio_lufs.get(lang, -16.0)
            total_asr_sec += duration

            # Aggregated QA metrics
            avg_coverage = (
                sum(q.coverage_score for q in qa_list) / max(1, len(qa_list)) if qa_list else 1.0
            )
            avg_cer = sum(q.cer for q in qa_list) / max(1, len(qa_list)) if qa_list else 0.0
            avg_wer = sum(q.wer for q in qa_list) / max(1, len(qa_list)) if qa_list else 0.0
            repetitions_count = sum(1 for q in qa_list if q.repetition_detected)
            all_passed = all(q.passed for q in qa_list) if qa_list else True

            per_language_data[lang] = {
                "audio_files": {
                    "full_lesson_mp3": f"{lang}/lesson.{lang}.mp3",
                    "full_lesson_wav": f"{lang}/lesson.{lang}.wav",
                    "sections": [
                        {
                            "section_id": it.section_id,
                            "slug": it.section_slug,
                            "title": it.section_title,
                            "mp3": f"{lang}/{it.section_id}-{it.section_slug}.{lang}.mp3",
                            "duration_seconds": round(it.duration_seconds, 2),
                            "loudness_lufs": it.loudness_lufs,
                        }
                        for it in items
                    ],
                },
                "total_duration_seconds": round(duration, 2),
                "integrated_loudness_lufs": round(lufs, 2),
                "qa_summary": {
                    "passed": all_passed,
                    "average_coverage": round(avg_coverage, 4),
                    "average_cer": round(avg_cer, 4),
                    "average_wer": round(avg_wer, 4),
                    "repetition_incidents": repetitions_count,
                    "sections_evaluated": len(qa_list),
                },
            }

        financials = self.calculate_cost(
            num_pages=doc_ir.total_pages,
            translate_chars=total_translate_chars,
            tts_chars=total_tts_chars,
            asr_duration_sec=total_asr_sec,
        )

        total_equations = sum(len(s.equations) for s in doc_ir.sections)
        total_tables = sum(len(s.tables) for s in doc_ir.sections)
        total_words = sum(s.word_count for s in doc_ir.sections)

        report = {
            "status": "completed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "execution_time_seconds": round(execution_time_sec, 2),
            "source_document": {
                "source_language": doc_ir.source_language,
                "total_pages": doc_ir.total_pages,
                "total_sections": len(doc_ir.sections),
                "total_words": total_words,
                "total_equations": total_equations,
                "total_tables": total_tables,
                "flagged_doubtful_blocks": doc_ir.flagged_blocks_count,
                "glossary_terms_count": len(doc_ir.glossary),
            },
            "target_languages": target_languages,
            "financial_audit": financials,
            "languages": per_language_data,
        }

        output_dir.mkdir(parents=True, exist_ok=True)
        report_file = output_dir / "report.json"
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        return report


report_auditor = ReportAuditor()
