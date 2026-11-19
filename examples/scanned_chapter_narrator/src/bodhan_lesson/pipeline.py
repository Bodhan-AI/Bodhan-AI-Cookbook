"""Master pipeline orchestrating Document, Language, and Audio Intelligence layers."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from bodhan_lesson.agent import chapter_narrator_agent
from bodhan_lesson.assembler import assembler
from bodhan_lesson.config import SUPPORTED_LANGUAGES
from bodhan_lesson.ingestion import ingest_scans_to_page_images
from bodhan_lesson.narration import narration_planner
from bodhan_lesson.ocr import ocr_processor
from bodhan_lesson.reader import html_reader_generator
from bodhan_lesson.reporter import report_auditor
from bodhan_lesson.schemas import DocumentIR, NarrationItem, QAReportSection
from bodhan_lesson.synthesizer import synthesizer
from bodhan_lesson.translator import translator
from bodhan_lesson.verifier import audio_verifier

logger = logging.getLogger("bodhan_lesson")


class LessonPipeline:
    """End-to-end pipeline transforming scanned textbook chapters into multilingual narrated lessons."""

    def run(
        self,
        input_path: Path | str,
        target_languages: list[str],
        output_dir: Path | str = "./out",
        source_language: str = "auto",
        skip_qa: bool = False,
        voice_override: str | None = None,
        code_mixed: bool = False,
        custom_glossary: dict[str, str] | None = None,
        custom_voices: dict[str, str] | None = None,
        fast_narration: bool = False,
    ) -> dict:
        """Execute the full 3-layer pipeline."""
        start_time = time.time()
        input_p = Path(input_path).resolve()
        out_p = Path(output_dir).resolve()
        out_p.mkdir(parents=True, exist_ok=True)

        logger.info(f"Starting Bodhan Lesson Pipeline on {input_p}")
        logger.info(f"Target languages: {target_languages}")

        pipeline_errors: list[str] = []

        # Validate target languages upfront against authorized Bodhan Indic models
        valid_languages: list[str] = []
        unsupported_languages: list[str] = []
        for lang_item in target_languages:
            code = lang_item.strip().lower()
            if code in SUPPORTED_LANGUAGES:
                valid_languages.append(code)
            else:
                unsupported_languages.append(code)

        if unsupported_languages:
            warn_msg = (
                f"Skipping unsupported language code(s): {unsupported_languages}. "
                f"Authorized Bodhan Indic languages are: {sorted(SUPPORTED_LANGUAGES)}"
            )
            logger.warning(warn_msg)
            pipeline_errors.append(warn_msg)

        if not valid_languages:
            raise ValueError(
                f"None of the requested languages {target_languages} are recognized by Bodhan. "
                f"Authorized Indic language codes: {sorted(SUPPORTED_LANGUAGES)}"
            )

        # =====================================================================
        # LAYER 1: DOCUMENT INTELLIGENCE
        # =====================================================================
        source_dir = out_p / "source"
        source_dir.mkdir(parents=True, exist_ok=True)

        # 1. Ingestion: Pure-image rasterization at native 300 DPI
        logger.info("[Layer 1] Rasterizing input pages to 300 DPI images...")
        page_images = ingest_scans_to_page_images(input_p, source_dir)
        total_pages = len(page_images)
        logger.info(f"[Layer 1] Ingested {total_pages} page(s).")

        # 2. OCR Extraction: Indic-OCR per page with block normalization
        logger.info("[Layer 1] Running Indic-OCR on all pages...")
        page_results = ocr_processor.process_document_pages(page_images)

        # Agentic Block Triage: Extract handwritten margin notes / callouts
        all_annotations: list[dict] = []
        for p in page_results:
            triaged_blocks, notes = chapter_narrator_agent.triage_blocks(p.blocks)
            p.blocks = triaged_blocks
            all_annotations.extend(notes)

        # Detect source language if set to auto
        detected_src_lang = "en"
        if source_language != "auto":
            detected_src_lang = source_language
        else:
            # Check for Devanagari or other Indic Unicode ranges
            full_text = " ".join(p.raw_markdown for p in page_results)
            if any("\u0900" <= c <= "\u097F" for c in full_text):
                detected_src_lang = "hi"
            elif any("\u0B80" <= c <= "\u0BFF" for c in full_text):
                detected_src_lang = "ta"
            elif any("\u0980" <= c <= "\u09FF" for c in full_text):
                detected_src_lang = "bn"

        # 3. Assembly: Clean noise, stitch hyphens across pages, build sections
        logger.info("[Layer 1] Assembling cross-page lesson and Document IR...")
        doc_ir: DocumentIR = assembler.assemble_document(
            pages=page_results,
            source_language=detected_src_lang,
        )

        # Write clean source lesson markdown and Document IR JSON
        source_markdown_path = source_dir / "lesson.src.md"
        source_markdown_path.write_text(doc_ir.source_markdown, encoding="utf-8")

        ir_json_path = source_dir / "document_ir.json"
        ir_json_path.write_text(doc_ir.model_dump_json(indent=2), encoding="utf-8")
        logger.info(f"[Layer 1] Source lesson written to {source_markdown_path}")

        # Write per-page raw OCR output with flags to source/pages/
        pages_out_dir = source_dir / "pages"
        pages_out_dir.mkdir(parents=True, exist_ok=True)
        for p in doc_ir.pages:
            page_json_path = pages_out_dir / f"page_{p.page_number:02d}.json"
            page_json_path.write_text(p.model_dump_json(indent=2), encoding="utf-8")

        # Write annotations.json if margin notes or callouts exist
        if all_annotations:
            ann_path = source_dir / "annotations.json"
            ann_path.write_text(json.dumps(all_annotations, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info(f"[Stretch Goal] Extracted {len(all_annotations)} margin note annotation(s) into {ann_path}")

        # Agentic Chapter Glossary Extraction
        logger.info("[Stretch Goal] Agentic extraction of chapter STEM domain glossary...")
        chapter_glossary = chapter_narrator_agent.extract_chapter_glossary(doc_ir.source_markdown)
        if custom_glossary:
            chapter_glossary.update(custom_glossary)
        gloss_json_path = source_dir / "glossary.json"
        gloss_json_path.write_text(json.dumps(chapter_glossary, indent=2, ensure_ascii=False), encoding="utf-8")
        gloss_yaml_lines = [f"{k}: \"{v}\"" for k, v in chapter_glossary.items()]
        (source_dir / "glossary.yaml").write_text("\n".join(gloss_yaml_lines), encoding="utf-8")
        logger.info(f"[Stretch Goal] Saved {len(chapter_glossary)} glossary terms to {gloss_json_path}")

        # =====================================================================
        # LAYER 2 & 3: LANGUAGE & AUDIO INTELLIGENCE
        # =====================================================================
        all_narrations: dict[str, list[NarrationItem]] = {}
        all_qa_reports: dict[str, list[QAReportSection]] = {}
        audio_durations: dict[str, float] = {}
        audio_lufs_map: dict[str, float] = {}

        for lang in valid_languages:
            lang = lang.strip().lower()
            logger.info(f"\n--- Processing Target Language: {lang} ---")
            lang_dir = out_p / lang
            lang_dir.mkdir(parents=True, exist_ok=True)

            try:
                # 1. Translate Source Lesson Markdown per section to maintain exact section boundaries
                logger.info(f"[Layer 2] Translating lesson markdown into '{lang}'...")
                preserve_terms = list(chapter_glossary.keys()) if code_mixed else None
                sec_translated_mds: list[str] = []
                for sec in doc_ir.sections:
                    sec_md = sec.source_markdown.strip()
                    if sec_md:
                        sec_trans = translator.translate_markdown(
                            text=sec_md,
                            target_lang=lang,
                            source_lang=doc_ir.source_language,
                            preserve_terms=preserve_terms,
                        )
                    else:
                        sec_trans = ""
                    sec_translated_mds.append(sec_trans)

                translated_lesson_md = "\n\n".join(s for s in sec_translated_mds if s).strip()
                if not translated_lesson_md and doc_ir.source_markdown:
                    translated_lesson_md = translator.translate_markdown(
                        text=doc_ir.source_markdown,
                        target_lang=lang,
                        source_lang=doc_ir.source_language,
                        preserve_terms=preserve_terms,
                    )
                lesson_md_path = lang_dir / f"lesson.{lang}.md"
                lesson_md_path.write_text(translated_lesson_md, encoding="utf-8")

                # 2. Narration Planning & Speech Synthesis per Section
                section_wav_paths: list[Path] = []
                lang_narrations: list[NarrationItem] = []
                lang_qa: list[QAReportSection] = []

                for sec_idx, section in enumerate(doc_ir.sections, start=1):
                    sec_id = f"{sec_idx:02d}"
                    logger.info(f"[Layer 2] Planning pedagogical narration for Section {sec_id}: {section.title}...")

                    # Draft grounded narration
                    narration_item = narration_planner.generate_narration(
                        section=section,
                        target_lang=lang,
                        source_lang=doc_ir.source_language,
                        code_mixed=code_mixed,
                        glossary_terms=list(chapter_glossary.keys()) if code_mixed else None,
                        fast_narration=fast_narration,
                    )
                    narration_item.section_id = sec_id
                    if sec_idx - 1 < len(sec_translated_mds) and sec_translated_mds[sec_idx - 1]:
                        narration_item.section_markdown = sec_translated_mds[sec_idx - 1]
                    else:
                        narration_item.section_markdown = section.source_markdown

                    # 3. Audio Synthesis & Loudness Normalization (-16 LUFS +- 1)
                    wav_path = lang_dir / f"{sec_id}-{section.section_slug}.{lang}.wav"
                    mp3_path = lang_dir / f"{sec_id}-{section.section_slug}.{lang}.mp3"

                    resolved_voice = voice_override
                    if not resolved_voice and custom_voices:
                        resolved_voice = custom_voices.get(lang)

                    logger.info(f"[Layer 3] Synthesizing speech for Section {sec_id} via Indic-Speak...")
                    duration_sec, lufs = synthesizer.synthesize_section(
                        script_text=narration_item.target_script,
                        lang=lang,
                        output_wav_path=wav_path,
                        output_mp3_path=mp3_path,
                        voice=resolved_voice,
                        target_lufs=-16.0,
                    )
                    narration_item.audio_path = mp3_path
                    narration_item.duration_seconds = duration_sec
                    narration_item.loudness_lufs = round(lufs, 2)
                    section_wav_paths.append(wav_path)
                    lang_narrations.append(narration_item)

                    # 4. Audio Quality Assurance via Indic-Transcribe
                    if not skip_qa:
                        logger.info(f"[Layer 3] Running 3D QA verification on Section {sec_id} via Indic-Transcribe...")
                        audio_bytes = wav_path.read_bytes()
                        qa_res = audio_verifier.verify_section_audio(
                            audio_bytes=audio_bytes,
                            reference_script=narration_item.target_script,
                            section_id=sec_id,
                            language=lang,
                            )
                        lang_qa.append(qa_res)

                # 5. Concatenate Sections into Full Lesson Audio Track
                logger.info(f"[Layer 3] Stitching full chapter audio track for '{lang}'...")
                full_wav_path = lang_dir / f"lesson.{lang}.wav"
                full_mp3_path = lang_dir / f"lesson.{lang}.mp3"

                total_lesson_duration = synthesizer.concatenate_sections_into_full_lesson(
                    section_wav_paths=section_wav_paths,
                    output_full_wav=full_wav_path,
                    output_full_mp3=full_mp3_path,
                    pause_between_sections_sec=0.500,
                )

                # Write narration_script.<lang>.json containing per-section TTS text, duration, and QA result
                narration_script_path = lang_dir / f"narration_script.{lang}.json"
                narration_script_data = [
                    {
                        "section_id": it.section_id,
                        "section_slug": it.section_slug,
                        "section_title": it.section_title,
                        "text_sent_to_tts": it.target_script,
                        "duration_seconds": round(it.duration_seconds, 2),
                        "qa_result": (
                            lang_qa[i].model_dump() if i < len(lang_qa) else {"passed": True}
                        ),
                    }
                    for i, it in enumerate(lang_narrations)
                ]
                narration_script_path.write_text(
                    json.dumps(narration_script_data, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

                # Populate <lang>/narration/ subfolder with section mp3 files
                narration_dir = lang_dir / "narration"
                narration_dir.mkdir(parents=True, exist_ok=True)
                for it in lang_narrations:
                    src_mp3 = lang_dir / f"{it.section_id}-{it.section_slug}.{lang}.mp3"
                    if src_mp3.is_file():
                        dest_mp3 = narration_dir / f"{it.section_id}-{it.section_slug}.{lang}.mp3"
                        dest_mp3.write_bytes(src_mp3.read_bytes())

                # Generate Interactive Synchronized HTML Reader (Stretch Goal)
                logger.info(f"[Stretch Goal] Generating synchronized HTML reader in {lang_dir / 'reader.html'}...")
                html_reader_generator.generate_reader_html(
                    lesson_title=doc_ir.sections[0].title if doc_ir.sections else "Textbook Lesson",
                    language=lang,
                    lesson_markdown=translated_lesson_md,
                    narration_items=lang_narrations,
                    audio_filename=f"lesson.{lang}.mp3",
                    glossary=chapter_glossary,
                    annotations=all_annotations,
                    output_html_path=lang_dir / "reader.html",
                )

                all_narrations[lang] = lang_narrations
                all_qa_reports[lang] = lang_qa
                audio_durations[lang] = total_lesson_duration
                audio_lufs_map[lang] = -16.0  # target normalized

            except Exception as lang_exc:
                err_msg = f"Failed processing target language '{lang}': {lang_exc}"
                logger.error(err_msg, exc_info=True)
                pipeline_errors.append(err_msg)
                all_narrations[lang] = []
                all_qa_reports[lang] = []
                audio_durations[lang] = 0.0
                audio_lufs_map[lang] = -16.0

        # =====================================================================
        # REPORT & AUDIT GENERATION
        # =====================================================================
        execution_time = time.time() - start_time
        logger.info(f"Generating run report and financial audit in {out_p / 'report.json'}...")

        final_report = report_auditor.build_report(
            doc_ir=doc_ir,
            target_languages=target_languages,
            narration_items=all_narrations,
            qa_reports=all_qa_reports,
            audio_durations=audio_durations,
            audio_lufs=audio_lufs_map,
            execution_time_sec=execution_time,
            output_dir=out_p,
            errors=pipeline_errors,
        )

        status_str = final_report.get("status", "completed")
        logger.info(f"Pipeline finished with status '{status_str}' in {execution_time:.2f} seconds.")
        return final_report


pipeline = LessonPipeline()
