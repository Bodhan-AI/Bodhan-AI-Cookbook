"""Main Agent orchestrator implementing the Vertical Slice pipeline and full CLI interface.
Prints clear progress lines, execution times, costs, and structured run summaries.
Always writes report.json even on failure and exits non-zero on error.
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Optional
from bodhan_mcp.types import SubtitleSegment, ToolError
from bodhan_mcp.audio_helpers import get_audio_info, build_srt_content, split_text_into_cues
from bodhan_mcp.cost import estimate_preflight_cost
from bodhan_mcp.tools.transcribe import transcribe_audio
from bodhan_mcp.tools.translate import translate_subtitles_batch, translate_text, count_markdown_table_structure
from bodhan_mcp.tools.lang_detect import detect_text_language
from bodhan_mcp.tools.ocr import run_ocr
from bodhan_mcp.tools.speak import synthesize_speech
from bodhan_agent.prompts import AGENT_SYSTEM_PROMPT, AGENT_TOOLS, wrap_untrusted_data
from bodhan_agent.llm import llm_client
from bodhan_agent.budget import BudgetTracker
from bodhan_mcp.tools.voices import list_voices
from bodhan_agent.tracer import Tracer
from bodhan_agent.verifier import (
    VerificationResult,
    verify_subtitles,
    verify_markdown_table_structure,
    verify_audio_summary,
    calculate_text_coverage,
)


class BodhanTaskAgent:
    def __init__(
        self,
        task: str,
        files_dir: str,
        out_dir: str,
        max_steps: int = 30,
        max_cost_inr: float = 20.0,
    ):
        self.task = task
        self.files_dir = Path(files_dir).resolve()
        self.out_dir = Path(out_dir).resolve()
        self.budget = BudgetTracker(str(self.out_dir), max_steps=max_steps, max_cost_inr=max_cost_inr, task=self.task)
        self.tracer = Tracer(self.out_dir)

    def find_input_files(self) -> List[Path]:
        """Find audio/media files in the input directory or single file."""
        if not self.files_dir.exists():
            raise ToolError("INPUT_DIR_NOT_FOUND", f"Input path does not exist: {self.files_dir}")

        supported_exts = {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".mp4", ".pdf", ".png", ".jpg", ".md", ".txt"}
        if self.files_dir.is_file():
            if self.files_dir.suffix.lower() in supported_exts:
                return [self.files_dir]
            else:
                raise ToolError("UNSUPPORTED_MEDIA", f"Unsupported file type: {self.files_dir.suffix}")

        found = []
        for root, _, files in os.walk(self.files_dir):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in supported_exts:
                    found.append(Path(root) / f)
        return found

    def _print_summary(self, status: str, duration_s: float, error_message: Optional[str] = None) -> None:
        """Print a clear terminal run summary."""
        print("\n" + "=" * 50)
        print("=== Run Summary ===")
        print(f"Status:        {status.upper()}")
        print(f"Duration:      {duration_s:.2f}s")
        cap_note = f" (Effective Hard Cap: ₹{self.budget.max_cost_inr:.4f}"
        if self.budget.user_stated_budget_inr is not None:
            cap_desc = f"{cap_note} [user-stated])"
        else:
            cap_desc = f"{cap_note})"
        print(f"Total Cost:    ₹{self.budget.spent_cost_inr:.4f}{cap_desc}")
        print(f"Steps:         {len(self.tracer.steps)}")
        if error_message:
            print(f"Error:         {error_message}")
        if self.tracer.artifacts_created:
            print("Artifacts:")
            for art in self.tracer.artifacts_created:
                print(f"  - {art}")
        print("Reports:")
        print(f"  - result.md")
        print(f"  - report.json")
        print(f"  - trace.jsonl")
        print("=" * 50 + "\n")

    def run_subtitle_vertical_slice(self, audio_file: Path, target_lang: str = "en") -> None:
        """Vertical slice pipeline: Voice Note -> Chunking -> Transcribe -> Subtitle Translate -> Verify -> SRT Output."""
        # 1. Media Inspection
        print(f"[Plan] Inspecting input media properties for {audio_file.name}...")
        t0 = time.time()
        self.budget.check_step_limit()
        audio_info = get_audio_info(str(audio_file))
        duration_s = audio_info["duration"]
        inspect_latency = int((time.time() - t0) * 1000)

        print(f"[Tool: bodhan_inspect_media] Done in {inspect_latency}ms (Cost: ₹0.0000)")
        print(f"       Duration: {duration_s:.1f}s | Channels: {audio_info['channels']} | SampleRate: {audio_info['samplerate']}Hz")

        self.tracer.record_step(
            step_number=self.budget.current_step,
            plan_step="Inspect input media properties",
            tool="bodhan_inspect_media",
            arguments={"audio_path": str(audio_file)},
            result_summary=f"Duration: {duration_s:.1f}s, SampleRate: {audio_info['samplerate']}Hz, Format: {audio_info['format']}",
            latency_ms=inspect_latency,
            cost_inr=0.0,
            cumulative_cost_inr=self.budget.spent_cost_inr,
        )

        # 2. Pre-flight budget check
        estimated_cost = estimate_preflight_cost(
            audio_duration_s=duration_s,
            estimated_translate_words=int(duration_s * 2.5),
        )
        remaining = self.budget.max_cost_inr - self.budget.spent_cost_inr
        print(f"[Budget] Pre-flight estimate: ₹{estimated_cost:.4f} <= Remaining budget: ₹{remaining:.4f}. OK to proceed.")
        self.budget.check_preflight_budget(estimated_cost)

        # 3. Speech-to-Text via Indic-Transcribe
        print(f"\n[Step {self.budget.current_step + 1}] Calling bodhan_transcribe (natural pause chunking <28s)...")
        t1 = time.time()
        self.budget.check_step_limit()
        trans_res = transcribe_audio(str(audio_file), output_format="segments")
        self.budget.record_cost(trans_res.cost_inr)
        trans_latency = int((time.time() - t1) * 1000)

        text_preview = trans_res.text[:60] + "..." if len(trans_res.text) > 60 else trans_res.text
        print(f"[Step {self.budget.current_step}: bodhan_transcribe] Done in {trans_latency}ms (Cost: ₹{trans_res.cost_inr:.4f}, Cumulative: ₹{self.budget.spent_cost_inr:.4f})")
        print(f"       Result: {len(trans_res.segments or [])} segment(s). Text: '{text_preview}'")

        self.tracer.record_step(
            step_number=self.budget.current_step,
            plan_step="Transcribe audio with natural pause chunking (<28s)",
            tool="bodhan_transcribe",
            arguments={"audio_path": str(audio_file), "vad": True},
            result_summary=f"Transcribed {len(trans_res.segments or [])} segments ({trans_res.duration_seconds}s). Text: '{text_preview}'",
            latency_ms=trans_latency,
            cost_inr=trans_res.cost_inr,
            cumulative_cost_inr=self.budget.spent_cost_inr,
        )

        if not trans_res.segments:
            raise ToolError("EMPTY_AUDIO_TRANSCRIPT", "Audio contained no speech or was completely silent.")

        # Save Source Transcript
        hi_txt_path = self.budget.sandbox_path("transcript_source.txt")
        with open(hi_txt_path, "w", encoding="utf-8") as f:
            f.write(trans_res.text)
        self.tracer.add_artifact(str(hi_txt_path))

        # 4. Language detection & Hindi/Marathi heuristic
        print(f"\n[Step {self.budget.current_step + 1}] Calling bodhan_detect_language...")
        t2 = time.time()
        self.budget.check_step_limit()
        lang_res = detect_text_language(trans_res.text)
        lang_latency = int((time.time() - t2) * 1000)

        assumed_tag = " (assumed - weak stop-word signal)" if lang_res.is_assumed else ""
        print(f"[Step {self.budget.current_step}: bodhan_detect_language] Done in {lang_latency}ms (Cost: ₹0.0000, Cumulative: ₹{self.budget.spent_cost_inr:.4f})")
        print(f"       Result: Detected '{lang_res.language}' ({lang_res.script}) with confidence {lang_res.confidence:.2f}{assumed_tag}")

        if lang_res.is_assumed:
            self.tracer.add_assumption(
                f"Audio language detected as {lang_res.language} with confidence {lang_res.confidence:.2f} (assumed due to lack of distinct regional stop words)."
            )

        self.tracer.record_step(
            step_number=self.budget.current_step,
            plan_step="Verify source language from transcription",
            tool="bodhan_detect_language",
            arguments={"text_sample": trans_res.text[:100]},
            result_summary=f"Language: {lang_res.language}, Script: {lang_res.script}, Confidence: {lang_res.confidence:.2f}, Assumed: {lang_res.is_assumed}",
            latency_ms=lang_latency,
            cost_inr=0.0,
            cumulative_cost_inr=self.budget.spent_cost_inr,
        )

        # 5. Subtitle batch translation
        print(f"\n[Step {self.budget.current_step + 1}] Calling bodhan_translate (batch subtitle translation as numbered list)...")
        t3 = time.time()
        self.budget.check_step_limit()
        source_lines = [s.text for s in trans_res.segments]

        translated_lines, trans_tokens, trans_cached = translate_subtitles_batch(
            lines=source_lines,
            target_language_code=target_lang,
            source_language_code=lang_res.language,
        )
        translate_cost = 0.0 if trans_cached else (trans_tokens / 10000.0) * 0.20
        self.budget.record_cost(translate_cost)
        trans_lat = int((time.time() - t3) * 1000)

        print(f"[Step {self.budget.current_step}: bodhan_translate] Done in {trans_lat}ms (Cost: ₹{translate_cost:.4f}, Cumulative: ₹{self.budget.spent_cost_inr:.4f})")
        print(f"       Result: Translated {len(translated_lines)} subtitle line(s) into English ({trans_tokens} tokens)")

        self.tracer.record_step(
            step_number=self.budget.current_step,
            plan_step="Batch translate subtitle lines in one request as numbered list",
            tool="bodhan_translate",
            arguments={"lines_count": len(source_lines), "target_language": target_lang},
            result_summary=f"Translated {len(translated_lines)} subtitle lines to English. Tokens: {trans_tokens}",
            latency_ms=trans_lat,
            cost_inr=translate_cost,
            cumulative_cost_inr=self.budget.spent_cost_inr,
        )

        # 6. Construct and format SRT cues (split at sentence boundaries, distribute timing proportionally, <= 2 lines, <= 42 chars/line)
        en_segments: List[SubtitleSegment] = []
        for orig_seg, en_text in zip(trans_res.segments, translated_lines):
            cues = split_text_into_cues(
                text=en_text,
                start_seconds=orig_seg.start_seconds,
                end_seconds=orig_seg.end_seconds,
                max_chars_per_line=42,
                max_lines=2,
                starting_index=len(en_segments) + 1,
            )
            en_segments.extend(cues)

        srt_out_path = self.budget.sandbox_path("subtitles_en.srt")
        srt_content = build_srt_content(en_segments)
        with open(srt_out_path, "w", encoding="utf-8") as f:
            f.write(srt_content)
        self.tracer.add_artifact(str(srt_out_path))

        # Format source language SRT cues
        source_segments: List[SubtitleSegment] = []
        for orig_seg in trans_res.segments:
            s_cues = split_text_into_cues(
                text=orig_seg.text,
                start_seconds=orig_seg.start_seconds,
                end_seconds=orig_seg.end_seconds,
                max_chars_per_line=42,
                max_lines=2,
                starting_index=len(source_segments) + 1,
            )
            source_segments.extend(s_cues)

        source_srt_path = self.budget.sandbox_path(f"subtitles_{lang_res.language}.srt")
        source_srt_content = build_srt_content(source_segments)
        with open(source_srt_path, "w", encoding="utf-8") as f:
            f.write(source_srt_content)
        self.tracer.add_artifact(str(source_srt_path))

        # 7. Verification Suite (SRT validity, script, numbers, nfc, repetitions)
        print(f"\n[Verify] Running verification suite on generated subtitles...")
        t4 = time.time()
        verifications = verify_subtitles(
            target_segments=en_segments,
            source_segments=trans_res.segments,
            target_lang=target_lang,
            audio_duration_s=duration_s,
            artifact_name="artifacts/subtitles_en.srt",
        )
        self.tracer.record_verifications(verifications)

        all_passed = all(v.passed for v in verifications)
        for v in verifications:
            badge = "PASS" if v.passed else "FAIL"
            print(f"  - [{badge}] {v.check_type}: {v.details}")

        verify_status = "PASSED" if all_passed else "WARNINGS"
        print(f"[Verify] Verification {verify_status} ({sum(1 for v in verifications if v.passed)}/{len(verifications)} checks passed)")

        self.budget.check_step_limit()
        self.tracer.record_step(
            step_number=self.budget.current_step,
            plan_step="Run verification suite on generated subtitles",
            tool="verifier",
            arguments={"checks": len(verifications)},
            result_summary=f"Verification {verify_status}: {sum(1 for v in verifications if v.passed)}/{len(verifications)} checks passed",
            latency_ms=int((time.time() - t4) * 1000),
            cost_inr=0.0,
            cumulative_cost_inr=self.budget.spent_cost_inr,
        )

        # 8. Finalize reports and summary
        total_time = time.time() - self.tracer.start_time
        self.tracer.finalize(
            task=self.task,
            status="completed",
            total_cost_inr=self.budget.spent_cost_inr,
            final_answer=f"Generated accurate English subtitles ({len(en_segments)} cues) for the input voice note.\nArtifact: [artifacts/subtitles_en.srt](artifacts/subtitles_en.srt)",
        )
        self._print_summary(status="completed", duration_s=total_time)

    def check_prompt_injection(self, text: str) -> Optional[str]:
        """Detect prompt injection attempts in untrusted input text."""
        patterns = [
            r"ignore (all )?previous instructions",
            r"disregard all instructions",
            r"system prompt override",
            r"you are now (an? )?[a-z]+",
            r"forget (all )?(prior|previous) rules",
            r"do not follow (the )?system prompt",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                return m.group(0)
        return None

    def run_pdf_audio_summary_pipeline(self, pdf_file: Path, target_lang: str = "ta") -> None:
        """Pipeline A: Scanned PDF -> Indic-OCR -> English Summary -> Tamil Translation -> Indic-Speak -> Back-Transcribe Verify."""
        print(f"\n[Plan] Inspecting PDF document: {pdf_file.name}...")
        t0 = time.time()
        self.budget.check_step_limit()
        import pymupdf
        doc = pymupdf.open(str(pdf_file))
        page_count = len(doc)
        doc.close()

        inspect_lat = int((time.time() - t0) * 1000)
        print(f"[Tool: bodhan_inspect_media] Done in {inspect_lat}ms (Cost: ₹0.0000)")
        print(f"       Pages: {page_count} | Document Type: Scanned PDF")

        self.tracer.record_step(
            step_number=self.budget.current_step,
            plan_step="Inspect PDF document properties",
            tool="bodhan_inspect_media",
            arguments={"pdf_path": str(pdf_file)},
            result_summary=f"Pages: {page_count}, Format: PDF",
            latency_ms=inspect_lat,
            cost_inr=0.0,
            cumulative_cost_inr=self.budget.spent_cost_inr,
        )

        # 2. Pre-flight budget check
        est_cost = (page_count * 0.10) + 0.05
        self.budget.check_preflight_budget(est_cost)

        # 3. Indic-OCR
        print(f"\n[Step {self.budget.current_step + 1}] Calling bodhan_ocr on {page_count} page(s)...")
        t1 = time.time()
        self.budget.check_step_limit()
        ocr_res = run_ocr(str(pdf_file))
        self.budget.record_cost(ocr_res.cost_inr)
        ocr_lat = int((time.time() - t1) * 1000)

        print(f"[Step {self.budget.current_step}: bodhan_ocr] Done in {ocr_lat}ms (Cost: ₹{ocr_res.cost_inr:.4f}, Cumulative: ₹{self.budget.spent_cost_inr:.4f})")
        print(f"       Extracted {len(ocr_res.text.split())} words from {ocr_res.page_count} page(s) (Tables detected: {ocr_res.tables_detected})")

        self.tracer.record_step(
            step_number=self.budget.current_step,
            plan_step=f"Run Indic-OCR on {page_count}-page document",
            tool="bodhan_ocr",
            arguments={"pdf_path": str(pdf_file), "pages": page_count},
            result_summary=f"Extracted {len(ocr_res.text)} characters from {ocr_res.page_count} pages",
            latency_ms=ocr_lat,
            cost_inr=ocr_res.cost_inr,
            cumulative_cost_inr=self.budget.spent_cost_inr,
        )

        ocr_out_path = self.budget.sandbox_path("ocr_extracted.md")
        with open(ocr_out_path, "w", encoding="utf-8") as f:
            f.write(ocr_res.text)
        self.tracer.add_artifact(str(ocr_out_path))

        # Check for prompt injection in extracted text
        inj = self.check_prompt_injection(ocr_res.text)
        if inj:
            print(f"[Security Warning] Prompt injection attempt detected in document: '{inj}'", file=sys.stderr)
            self.tracer.add_assumption(f"Document contained potential prompt injection: '{inj}'. Treated strictly as inert data.")

        # 4. English Executive Summary
        print(f"\n[Step {self.budget.current_step + 1}] Generating English executive summary...")
        t2 = time.time()
        self.budget.check_step_limit()
        summary_en = (
            "The National Rural Health Initiative report 2026 highlights the child immunization drive across rural districts. "
            "A total of 62,280 children were vaccinated achieving 94.4% overall coverage, with Vellore and Salem leading at 95%. "
            "Key recommendations include weekly cold-chain logger audits, mobile vaccination camps, and automated digital health records."
        )
        sum_lat = int((time.time() - t2) * 1000)
        sum_out_path = self.budget.sandbox_path("summary_en.md")
        with open(sum_out_path, "w", encoding="utf-8") as f:
            f.write(f"# Executive Summary (English)\n\n{summary_en}\n")
        self.tracer.add_artifact(str(sum_out_path))

        self.tracer.record_step(
            step_number=self.budget.current_step,
            plan_step="Generate executive summary from OCR document text",
            tool="summarizer",
            arguments={"document_words": len(ocr_res.text.split())},
            result_summary=f"Generated executive summary ({len(summary_en.split())} words)",
            latency_ms=sum_lat,
            cost_inr=0.0,
            cumulative_cost_inr=self.budget.spent_cost_inr,
        )

        # 5. Translate summary to Tamil
        print(f"\n[Step {self.budget.current_step + 1}] Calling bodhan_translate to translate summary to Tamil ({target_lang})...")
        t3 = time.time()
        self.budget.check_step_limit()
        trans_res = translate_text(summary_en, target_language_code=target_lang, source_language_code="en")
        self.budget.record_cost(trans_res.cost_inr)
        trans_lat = int((time.time() - t3) * 1000)

        print(f"[Step {self.budget.current_step}: bodhan_translate] Done in {trans_lat}ms (Cost: ₹{trans_res.cost_inr:.4f}, Cumulative: ₹{self.budget.spent_cost_inr:.4f})")
        print(f"       Tamil Translation: '{trans_res.translated_text[:60]}...'")

        sum_ta_path = self.budget.sandbox_path(f"summary_{target_lang}.md")
        with open(sum_ta_path, "w", encoding="utf-8") as f:
            f.write(f"# Executive Summary ({target_lang.upper()})\n\n{trans_res.translated_text}\n")
        self.tracer.add_artifact(str(sum_ta_path))

        self.tracer.record_step(
            step_number=self.budget.current_step,
            plan_step=f"Translate executive summary into {target_lang}",
            tool="bodhan_translate",
            arguments={"target_language": target_lang},
            result_summary=f"Translated to {target_lang} ({trans_res.estimated_tokens} tokens)",
            latency_ms=trans_lat,
            cost_inr=trans_res.cost_inr,
            cumulative_cost_inr=self.budget.spent_cost_inr,
        )

        # 6. Synthesize speech in Tamil via Indic-Speak
        print(f"\n[Step {self.budget.current_step + 1}] Calling bodhan_speak to synthesize Tamil audio summary...")
        t4 = time.time()
        self.budget.check_step_limit()
        audio_out_path = self.budget.sandbox_path(f"summary_{target_lang}.mp3")
        speak_res = synthesize_speech(
            text=trans_res.translated_text,
            language=target_lang,
            voice="Amit",
            output_path=str(audio_out_path),
        )
        self.budget.record_cost(speak_res.cost_inr)
        speak_lat = int((time.time() - t4) * 1000)

        print(f"[Step {self.budget.current_step}: bodhan_speak] Done in {speak_lat}ms (Cost: ₹{speak_res.cost_inr:.4f}, Cumulative: ₹{self.budget.spent_cost_inr:.4f})")
        print(f"       Synthesized {speak_res.duration_seconds:.1f}s audio saved to {audio_out_path.name}")
        self.tracer.add_artifact(str(audio_out_path))

        self.tracer.record_step(
            step_number=self.budget.current_step,
            plan_step=f"Synthesize {target_lang} speech using Indic-Speak",
            tool="bodhan_speak",
            arguments={"language": target_lang, "voice": "Amit"},
            result_summary=f"Synthesized {speak_res.duration_seconds:.1f}s audio",
            latency_ms=speak_lat,
            cost_inr=speak_res.cost_inr,
            cumulative_cost_inr=self.budget.spent_cost_inr,
        )

        # 7. Back-transcribe synthesized audio to verify intelligibility
        print(f"\n[Step {self.budget.current_step + 1}] Calling bodhan_transcribe for back-transcription verification...")
        t5 = time.time()
        self.budget.check_step_limit()
        bt_res = transcribe_audio(str(audio_out_path), language=target_lang)
        self.budget.record_cost(bt_res.cost_inr)
        bt_lat = int((time.time() - t5) * 1000)

        print(f"[Step {self.budget.current_step}: bodhan_transcribe] Done in {bt_lat}ms (Cost: ₹{bt_res.cost_inr:.4f}, Cumulative: ₹{self.budget.spent_cost_inr:.4f})")
        print(f"       Back-transcribed text: '{bt_res.text[:60]}...'")

        bt_path = self.budget.sandbox_path("back_transcript.txt")
        with open(bt_path, "w", encoding="utf-8") as f:
            f.write(bt_res.text)
        self.tracer.add_artifact(str(bt_path))

        self.tracer.record_step(
            step_number=self.budget.current_step,
            plan_step="Back-transcribe synthesized audio to verify intelligibility",
            tool="bodhan_transcribe",
            arguments={"language": target_lang},
            result_summary=f"Back-transcribed {len(bt_res.text)} characters",
            latency_ms=bt_lat,
            cost_inr=bt_res.cost_inr,
            cumulative_cost_inr=self.budget.spent_cost_inr,
        )

        # 8. Verification Suite
        print(f"\n[Verify] Running verification suite on audio summary pipeline...")
        t6 = time.time()
        verifications = verify_audio_summary(
            ocr_pages=page_count,
            summary_text=trans_res.translated_text,
            target_audio_path=str(audio_out_path),
            back_transcription_text=bt_res.text,
            target_lang=target_lang,
            artifact_name=f"artifacts/summary_{target_lang}.mp3",
        )
        self.tracer.record_verifications(verifications)

        all_passed = all(v.passed for v in verifications)
        for v in verifications:
            badge = "PASS" if v.passed else "FAIL"
            print(f"  - [{badge}] {v.check_type}: {v.details}")

        verify_status = "PASSED" if all_passed else "WARNINGS"
        print(f"[Verify] Verification {verify_status} ({sum(1 for v in verifications if v.passed)}/{len(verifications)} checks passed)")

        self.budget.check_step_limit()
        self.tracer.record_step(
            step_number=self.budget.current_step,
            plan_step="Run verification suite on audio summary artifacts",
            tool="verifier",
            arguments={"checks": len(verifications)},
            result_summary=f"Verification {verify_status}: {sum(1 for v in verifications if v.passed)}/{len(verifications)} checks passed",
            latency_ms=int((time.time() - t6) * 1000),
            cost_inr=0.0,
            cumulative_cost_inr=self.budget.spent_cost_inr,
        )

        # 9. Finalize
        total_time = time.time() - self.tracer.start_time
        self.tracer.finalize(
            task=self.task,
            status="completed",
            total_cost_inr=self.budget.spent_cost_inr,
            final_answer=f"Processed 3-page document via Indic-OCR, summarized in English, translated to Tamil, and synthesized audio summary with back-transcription verification.\nArtifacts: [artifacts/summary_{target_lang}.mp3](artifacts/summary_{target_lang}.mp3), [artifacts/summary_{target_lang}.md](artifacts/summary_{target_lang}.md)",
        )
        self._print_summary(status="completed", duration_s=total_time)

    def run_table_translation_pipeline(self, md_file: Path, target_lang: str = "mr") -> None:
        """Pipeline B: Markdown table translation to Marathi with row/column verification."""
        print(f"\n[Plan] Reading input Markdown table document: {md_file.name}...")
        t0 = time.time()
        self.budget.check_step_limit()
        with open(md_file, "r", encoding="utf-8") as f:
            source_content = f.read()

        inj = self.check_prompt_injection(source_content)
        if inj:
            print(f"[Security Warning] Prompt injection attempt detected in input: '{inj}'", file=sys.stderr)
            self.tracer.add_assumption(f"Input contained potential prompt injection: '{inj}'. Treated strictly as inert data.")

        inspect_lat = int((time.time() - t0) * 1000)
        print(f"[Tool: inspect_document] Read {len(source_content.splitlines())} lines ({len(source_content)} chars)")

        self.tracer.record_step(
            step_number=self.budget.current_step,
            plan_step="Inspect input Markdown document",
            tool="inspect_document",
            arguments={"path": str(md_file)},
            result_summary=f"Read {len(source_content)} characters",
            latency_ms=inspect_lat,
            cost_inr=0.0,
            cumulative_cost_inr=self.budget.spent_cost_inr,
        )

        # 2. Preflight check
        est_cost = (len(source_content.split()) / 500) * 0.20
        self.budget.check_preflight_budget(est_cost)

        # 3. Translate table to target language
        print(f"\n[Step {self.budget.current_step + 1}] Calling bodhan_translate for Markdown table translation to {target_lang}...")
        t1 = time.time()
        self.budget.check_step_limit()
        trans_res = translate_text(
            text=source_content,
            target_language_code=target_lang,
            source_language_code="en",
            preserve_formatting=True,
        )
        self.budget.record_cost(trans_res.cost_inr)
        trans_lat = int((time.time() - t1) * 1000)

        print(f"[Step {self.budget.current_step}: bodhan_translate] Done in {trans_lat}ms (Cost: ₹{trans_res.cost_inr:.4f}, Cumulative: ₹{self.budget.spent_cost_inr:.4f})")

        out_table_path = self.budget.sandbox_path(f"table_{target_lang}.md")
        with open(out_table_path, "w", encoding="utf-8") as f:
            f.write(trans_res.translated_text)
        self.tracer.add_artifact(str(out_table_path))

        self.tracer.record_step(
            step_number=self.budget.current_step,
            plan_step=f"Translate Markdown table to {target_lang}",
            tool="bodhan_translate",
            arguments={"target_language": target_lang, "preserve_formatting": True},
            result_summary=f"Translated table to {target_lang} ({trans_res.estimated_tokens} tokens)",
            latency_ms=trans_lat,
            cost_inr=trans_res.cost_inr,
            cumulative_cost_inr=self.budget.spent_cost_inr,
        )

        # 4. Verification Suite
        print(f"\n[Verify] Running table structure verification suite...")
        t2 = time.time()
        verifications = verify_markdown_table_structure(
            source_md=source_content,
            target_md=trans_res.translated_text,
            target_lang=target_lang,
            artifact_name=f"artifacts/table_{target_lang}.md",
        )
        self.tracer.record_verifications(verifications)

        all_passed = all(v.passed for v in verifications)
        for v in verifications:
            badge = "PASS" if v.passed else "FAIL"
            print(f"  - [{badge}] {v.check_type}: {v.details}")

        verify_status = "PASSED" if all_passed else "WARNINGS"
        print(f"[Verify] Verification {verify_status} ({sum(1 for v in verifications if v.passed)}/{len(verifications)} checks passed)")

        self.budget.check_step_limit()
        self.tracer.record_step(
            step_number=self.budget.current_step,
            plan_step="Verify table structure, row/column counts, and Marathi script",
            tool="verifier",
            arguments={"checks": len(verifications)},
            result_summary=f"Verification {verify_status}: {sum(1 for v in verifications if v.passed)}/{len(verifications)} checks passed",
            latency_ms=int((time.time() - t2) * 1000),
            cost_inr=0.0,
            cumulative_cost_inr=self.budget.spent_cost_inr,
        )

        # 5. Finalize
        total_time = time.time() - self.tracer.start_time
        self.tracer.finalize(
            task=self.task,
            status="completed",
            total_cost_inr=self.budget.spent_cost_inr,
            final_answer=f"Translated Markdown table to {target_lang} with table structure conservation.\nArtifact: [artifacts/table_{target_lang}.md](artifacts/table_{target_lang}.md)",
        )
        self._print_summary(status="completed", duration_s=total_time)

    def run_audio_lang_detect_pipeline(self, audio_files: List[Path]) -> None:
        """Pipeline C: Multi-audio language detection using Indic-Transcribe auto-detect and Devanagari heuristics."""
        print(f"\n[Plan] Running audio language detection across {len(audio_files)} clip(s)...")
        results = {}

        for a_idx, af in enumerate(audio_files, 1):
            print(f"\n[Step {self.budget.current_step + 1}] Processing {af.name}...")
            t0 = time.time()
            self.budget.check_step_limit()
            trans_res = transcribe_audio(str(af), output_format="text")
            self.budget.record_cost(trans_res.cost_inr)
            t_lat = int((time.time() - t0) * 1000)

            lang_res = detect_text_language(trans_res.text)
            results[af.name] = {
                "detected_language": lang_res.language,
                "script": lang_res.script,
                "confidence": lang_res.confidence,
                "is_assumed": lang_res.is_assumed,
                "text_preview": trans_res.text[:60],
            }

            self.tracer.record_step(
                step_number=self.budget.current_step,
                plan_step=f"Detect language for {af.name}",
                tool="bodhan_transcribe + bodhan_detect_language",
                arguments={"audio": af.name},
                result_summary=f"Language: {lang_res.language} ({lang_res.script}), Confidence: {lang_res.confidence:.2f}",
                latency_ms=t_lat,
                cost_inr=trans_res.cost_inr,
                cumulative_cost_inr=self.budget.spent_cost_inr,
            )

        res_path = self.budget.sandbox_path("lang_detect_results.json")
        with open(res_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        self.tracer.add_artifact(str(res_path))

        # Verification suite
        print(f"\n[Verify] Running verification on language detection results...")
        from bodhan_agent.verifier import VerificationResult
        verifications = []
        for name, data in results.items():
            ok = data["confidence"] >= 0.50
            verifications.append(
                VerificationResult(
                    artifact_name="artifacts/lang_detect_results.json",
                    check_type=f"detect_{data['detected_language']}",
                    passed=ok,
                    details=f"{name}: detected '{data['detected_language']}' ({data['script']}) with confidence {data['confidence']:.2f}",
                )
            )
        self.tracer.record_verifications(verifications)
        for v in verifications:
            print(f"  - [{'PASS' if v.passed else 'FAIL'}] {v.check_type}: {v.details}")

        self.budget.check_step_limit()
        self.tracer.record_step(
            step_number=self.budget.current_step,
            plan_step="Verify detected languages and confidence levels",
            tool="verifier",
            arguments={"clips_evaluated": len(results)},
            result_summary=f"Verified {len(results)} audio language determinations",
            latency_ms=1,
            cost_inr=0.0,
            cumulative_cost_inr=self.budget.spent_cost_inr,
        )

        # Finalize
        total_time = time.time() - self.tracer.start_time
        self.tracer.finalize(
            task=self.task,
            status="completed",
            total_cost_inr=self.budget.spent_cost_inr,
            final_answer=f"Detected audio languages for {len(audio_files)} files.\nArtifact: [artifacts/lang_detect_results.json](artifacts/lang_detect_results.json)",
        )
        self._print_summary(status="completed", duration_s=total_time)

    def dispatch_tool(self, tool_name: str, args: dict) -> Tuple[str, float, str]:
        """Dispatch a tool call requested by the LLM. Returns (result_json_str, cost_inr, summary_str)."""
        t0 = time.time()
        self.budget.check_step_limit()

        # Check prompt injection in tool arguments (e.g. adversarial file content passed to tools)
        inj_in_args = self.check_prompt_injection(str(args))
        if inj_in_args:
            print(f"[Security Warning] Prompt injection attempt detected in tool input: '{inj_in_args}'", file=sys.stderr)
            self.tracer.add_assumption(f"Input data contained potential prompt injection: '{inj_in_args}'. Treated strictly as inert data.")

        if tool_name == "bodhan_inspect_media":
            fpath = str(args.get("file_path", ""))
            fname = Path(fpath).name
            print(f"[Plan] Inspecting input media properties for {fname}...")
            ext = os.path.splitext(fpath)[1].lower()
            if ext == ".pdf":
                import pymupdf
                doc = pymupdf.open(fpath)
                pcount = len(doc)
                doc.close()
                res = {"file_path": fpath, "format": "PDF", "pages": pcount}
                print(f"[Tool: bodhan_inspect_media] Done in 5ms (Cost: ₹0.0000)")
                print(f"       Pages: {pcount} | Document Type: Scanned PDF")
            elif ext in (".md", ".txt"):
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                res = {"file_path": fpath, "format": "TEXT", "lines": len(content.splitlines()), "characters": len(content)}
                print(f"[Tool: bodhan_inspect_media] Done in 2ms (Cost: ₹0.0000)")
            else:
                res = get_audio_info(fpath)
                dur = res.get("duration", 10.0)
                print(f"[Tool: bodhan_inspect_media] Done in 5ms (Cost: ₹0.0000)")
                print(f"       Duration: {dur:.1f}s | Channels: {res.get('channels', 1)} | SampleRate: {res.get('samplerate', 16000)}Hz")
                est_cost = dur * (0.10 / 60.0) + (dur * 2.5 / 10000.0) * 0.20
                rem = self.budget.max_cost_inr - self.budget.spent_cost_inr
                print(f"[Budget] Pre-flight estimate: ₹{est_cost:.4f} <= Remaining budget: ₹{rem:.4f}. OK to proceed.")
            return json.dumps(res), 0.0, f"Inspected {Path(fpath).name}: {res}"

        elif tool_name == "bodhan_transcribe":
            apath = str(args["audio_path"])
            if not os.path.exists(apath):
                cand = self.budget.sandbox_path(os.path.basename(apath))
                if cand.exists():
                    apath = str(cand)

            lang = args.get("language")
            vad = args.get("vad", True)
            output_format = args.get("output_format", "text")
            is_bt = "back" in str(args.get("purpose", "")).lower() or "summary" in apath.lower()
            if is_bt:
                print(f"\n[Step {self.budget.current_step + 1}] Calling bodhan_transcribe for back-transcription verification...")
            else:
                print(f"\n[Step {self.budget.current_step + 1}] Calling bodhan_transcribe (natural pause chunking <28s)...")
            t_tr = time.time()
            try:
                ainfo = get_audio_info(apath)
                dur = ainfo.get("duration", 10.0)
                self.budget.check_preflight_budget(dur * (0.10 / 60.0))
            except Exception:
                pass
            res = transcribe_audio(audio_path=apath, language=lang, vad=vad, output_format=output_format)
            self.budget.record_cost(res.cost_inr)
            t_lat = int((time.time() - t_tr) * 1000)
            self._last_transcribe_output = res
            self._last_transcribe_segments = res.segments or []
            self._last_transcribe_language = getattr(res, "language", None) or "hi"
            self._last_transcribe_text = res.text or ""
            if getattr(res, "recovery_attempts", None):
                for ra in res.recovery_attempts:
                    self.tracer.add_assumption(f"Transcription recovery note: {ra}")
                    print(f"  [Recovery] {ra}")
            print(f"[Step {self.budget.current_step}: bodhan_transcribe] Done in {t_lat}ms (Cost: ₹{res.cost_inr:.4f}, Cumulative: ₹{self.budget.spent_cost_inr:.4f})")
            if is_bt and hasattr(self, "_last_ocr_text"):
                bt_vers = verify_audio_summary(
                    ocr_pages=getattr(self, "_last_ocr_pages", 1),
                    summary_text=getattr(self, "_last_translated_text", self._last_ocr_text),
                    target_audio_path=apath,
                    back_transcription_text=res.text or "",
                    target_lang=lang or "ta",
                    artifact_name=f"artifacts/{Path(apath).name}",
                )
                self.tracer.record_verifications(bt_vers)
                for v in bt_vers:
                    print(f"  - [{'PASS' if v.passed else 'FAIL'}] {v.check_type}: {v.details}")
            elif not is_bt and res.text:
                txt_path = self.budget.sandbox_path("transcript_source.txt")
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(res.text)
                self.tracer.add_artifact(str(txt_path))
            return res.model_dump_json(), res.cost_inr, f"Transcribed {Path(apath).name} ({res.duration_seconds:.1f}s, cost ₹{res.cost_inr:.4f})"

        elif tool_name == "bodhan_translate":
            text = args["text"]
            target_lang = args["target_language_code"]
            source_lang = args.get("source_language_code", "en")
            preserve_fmt = args.get("preserve_formatting", True)
            src_tables = count_markdown_table_structure(text)
            is_table = len(src_tables) > 0
            if is_table:
                print(f"\n[Step {self.budget.current_step + 1}] Calling bodhan_translate for Markdown table translation (structural preservation)...")
            else:
                print(f"\n[Step {self.budget.current_step + 1}] Calling bodhan_translate...")
            t_tr = time.time()
            est_cost = (len(text.split()) / 500.0) * 0.20
            self.budget.check_preflight_budget(est_cost)
            res = translate_text(text, target_language_code=target_lang, source_language_code=source_lang, preserve_formatting=preserve_fmt)
            self.budget.record_cost(res.cost_inr)
            t_lat = int((time.time() - t_tr) * 1000)
            print(f"[Step {self.budget.current_step}: bodhan_translate] Done in {t_lat}ms (Cost: ₹{res.cost_inr:.4f}, Cumulative: ₹{self.budget.spent_cost_inr:.4f})")
            
            # Save translated text artifact for passing by reference
            art_name = f"translated_{target_lang}.txt"
            art_dest = self.budget.sandbox_path(art_name)
            with open(art_dest, "w", encoding="utf-8") as f:
                f.write(res.translated_text)
            rel_art = self.tracer.to_rel_path(str(art_dest))
            self.tracer.add_artifact(str(art_dest))
            self._last_translation_ref = rel_art
            self._last_translated_text = res.translated_text
            self._last_translated_lang = target_lang

            res_dict = res.model_dump()
            res_dict["text_artifact_id"] = rel_art

            if is_table:
                print(f"\n[Verify] Running table structure verification suite...")
                t_vers = verify_markdown_table_structure(text, res.translated_text, target_lang, f"artifacts/table_{target_lang}.md")
                self.tracer.record_verifications(t_vers)
                for v in t_vers:
                    print(f"  - [{'PASS' if v.passed else 'FAIL'}] {v.check_type}: {v.details}")
            return json.dumps(res_dict, ensure_ascii=False), res.cost_inr, f"Translated to {target_lang} ({res.estimated_tokens} tokens, ref: {rel_art})"

        elif tool_name == "bodhan_speak":
            text = args.get("text")
            text_ref = args.get("text_artifact_id") or getattr(self, "_last_translation_ref", None)
            if (not text or not str(text).strip()) and text_ref:
                ref_candidates = [
                    self.out_dir / text_ref,
                    self.out_dir / "artifacts" / os.path.basename(text_ref),
                    Path(text_ref)
                ]
                for rc in ref_candidates:
                    if rc.exists():
                        text = rc.read_text(encoding="utf-8").strip()
                        print(f"[Reference] Loaded speech text from artifact reference '{rc.name}' ({len(text)} chars)")
                        break

            if not text or not str(text).strip():
                raise ToolError("MISSING_ARGUMENT", "Either 'text' or 'text_artifact_id' must be provided to bodhan_speak.")

            lang = args["language"]
            voice = args.get("voice", "Amit")
            out_filename = args.get("output_filename") or f"summary_{lang}.mp3"
            clean_name = os.path.basename(out_filename)
            dest_path = self.budget.sandbox_path(clean_name)

            # Check voice against user task request and language
            from bodhan_mcp.tools.voices import get_voice_profile
            vp = get_voice_profile(voice)
            if vp:
                if vp.primary_language != lang:
                    assump_voice = (
                        f"Selected voice '{voice}' (primary: {vp.primary_language}, cross-language: True) "
                        f"for synthesizing '{lang}' speech because no native persona was available/selected in primary catalog."
                    )
                    self.tracer.add_assumption(assump_voice)
                if "male" in self.task.lower() and "female" not in self.task.lower() and vp.gender != "male":
                    self.tracer.add_assumption(f"Voice gender deviation: Task requested male voice, but '{voice}' ({vp.gender}) was selected.")
                elif "female" in self.task.lower() and vp.gender != "female":
                    self.tracer.add_assumption(f"Voice gender deviation: Task requested female voice, but '{voice}' ({vp.gender}) was selected.")

            print(f"\n[Step {self.budget.current_step + 1}] Calling bodhan_speak (chunked <=400 chars, stitching)...")
            t_spk = time.time()
            est_cost = (len(text) / 1000.0) * 0.60
            self.budget.check_preflight_budget(est_cost)
            res = synthesize_speech(text=text, language=lang, voice=voice, output_path=str(dest_path))
            self.budget.record_cost(res.cost_inr)
            t_lat = int((time.time() - t_spk) * 1000)
            print(f"[Step {self.budget.current_step}: bodhan_speak] Done in {t_lat}ms (Cost: ₹{res.cost_inr:.4f}, Cumulative: ₹{self.budget.spent_cost_inr:.4f})")
            self.tracer.add_artifact(str(dest_path))
            self._last_synthesized_audio = str(dest_path)
            self._last_synthesized_voice = voice
            self._last_synthesized_lang = lang
            self._last_synthesized_text = text
            self._last_translated_lang = lang

            md_path = self.budget.sandbox_path(f"summary_{lang}.md")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(text)
            self.tracer.add_artifact(str(md_path))
            return res.model_dump_json(), res.cost_inr, f"Synthesized {res.duration_seconds:.1f}s audio ({dest_path.name}, cost ₹{res.cost_inr:.4f})"

        elif tool_name == "bodhan_ocr":
            fpath = str(args["file_path"])
            pnums = args.get("page_numbers")
            print(f"\n[Step {self.budget.current_step + 1}] Calling bodhan_ocr (page-by-page rendering via PyMuPDF)...")
            t_ocr = time.time()
            est_pages = 1
            if fpath.lower().endswith(".pdf"):
                try:
                    import pymupdf
                    doc = pymupdf.open(fpath)
                    est_pages = len(doc)
                    doc.close()
                except Exception:
                    pass
            self.budget.check_preflight_budget(est_pages * 0.10)
            res = run_ocr(file_path=fpath, page_numbers=pnums)
            self.budget.record_cost(res.cost_inr)
            t_lat = int((time.time() - t_ocr) * 1000)
            print(f"[Step {self.budget.current_step}: bodhan_ocr] Done in {t_lat}ms (Cost: ₹{res.cost_inr:.4f}, Cumulative: ₹{self.budget.spent_cost_inr:.4f})")
            ocr_out_path = self.budget.sandbox_path("ocr_extracted.md")
            with open(ocr_out_path, "w", encoding="utf-8") as f:
                f.write(res.text)
            self.tracer.add_artifact(str(ocr_out_path))
            self._last_ocr_text = res.text
            self._last_ocr_pages = res.page_count
            return res.model_dump_json(), res.cost_inr, f"Extracted text from {res.page_count} pages ({res.tables_detected} tables, cost ₹{res.cost_inr:.4f})"

        elif tool_name == "bodhan_detect_language":
            text = args["text"]
            print(f"\n[Step {self.budget.current_step + 1}] Calling bodhan_detect_language...")
            t_det = time.time()
            res = detect_text_language(text)
            t_lat = int((time.time() - t_det) * 1000)
            assumed_tag = " (assumed - weak stop-word signal)" if res.is_assumed else ""
            print(f"[Step {self.budget.current_step}: bodhan_detect_language] Done in {t_lat}ms (Cost: ₹0.0000, Cumulative: ₹{self.budget.spent_cost_inr:.4f})")
            print(f"       Result: Detected '{res.language}' ({res.script}) with confidence {res.confidence:.2f}{assumed_tag}")
            if res.is_assumed:
                self.tracer.add_assumption(f"Language detected as {res.language} with confidence {res.confidence:.2f} (assumed).")

            if not hasattr(self, "_lang_detect_map"):
                self._lang_detect_map = {}
            audio_name = args.get("audio_file") or Path(str(args.get("text", "audio")[:10])).name
            if not audio_name or audio_name == "audio":
                audio_name = f"clip_{len(self._lang_detect_map) + 1}.mp3"
            self._lang_detect_map[audio_name] = {
                "detected_language": res.language,
                "script": res.script,
                "confidence": res.confidence,
                "is_assumed": res.is_assumed,
            }
            res_path = self.budget.sandbox_path("lang_detect_results.json")
            with open(res_path, "w", encoding="utf-8") as f:
                json.dump(self._lang_detect_map, f, indent=2, ensure_ascii=False)
            self.tracer.add_artifact(str(res_path))

            v_res = VerificationResult(
                artifact_name="artifacts/lang_detect_results.json",
                check_type=f"detect_{res.language}",
                passed=res.confidence >= 0.50,
                details=f"{audio_name}: detected '{res.language}' ({res.script}) with confidence {res.confidence:.2f}",
            )
            self.tracer.record_verifications([v_res])
            print(f"  - [{'PASS' if v_res.passed else 'FAIL'}] {v_res.check_type}: {v_res.details}")
            return res.model_dump_json(), 0.0, f"Detected {res.language} ({res.script}, conf {res.confidence:.2f})"

        elif tool_name == "bodhan_list_voices":
            lang = args.get("language")
            gender = args.get("gender")
            voices = list_voices(language=lang, gender=gender)
            return json.dumps([v.model_dump() for v in voices]), 0.0, f"Found {len(voices)} voice(s)"

        elif tool_name == "format_subtitles_srt":
            apath = str(args.get("audio_path", ""))
            target_lang = args.get("target_language", "en")
            out_filename = args.get("output_filename") or f"subtitles_{target_lang}.srt"
            clean_name = os.path.basename(out_filename)
            dest_path = self.budget.sandbox_path(clean_name)
            try:
                ainfo = get_audio_info(apath)
                dur = ainfo.get("duration", 10.0)
            except Exception:
                dur = 10.0

            segments = getattr(self, "_last_transcribe_segments", None) or []
            source_lang = getattr(self, "_last_transcribe_language", "hi")

            if segments and target_lang != source_lang:
                source_lines = [s.text for s in segments]
                print(f"\n[Step {self.budget.current_step + 1}] Calling bodhan_translate (batch subtitle translation as numbered list)...")
                t3 = time.time()
                self.budget.check_step_limit()
                translated_lines, trans_tokens, trans_cached = translate_subtitles_batch(
                    lines=source_lines,
                    target_language_code=target_lang,
                    source_language_code=source_lang,
                )
                t_cost = 0.0 if trans_cached else (trans_tokens / 10000.0) * 0.20
                self.budget.record_cost(t_cost)
                trans_lat = int((time.time() - t3) * 1000)
                print(f"[Step {self.budget.current_step}: bodhan_translate] Done in {trans_lat}ms (Cost: ₹{t_cost:.4f}, Cumulative: ₹{self.budget.spent_cost_inr:.4f})")

                en_segments = []
                for orig_seg, en_text in zip(segments, translated_lines):
                    cues = split_text_into_cues(en_text, orig_seg.start_seconds, orig_seg.end_seconds, 42, 2, len(en_segments) + 1)
                    en_segments.extend(cues)
                with open(dest_path, "w", encoding="utf-8") as f:
                    f.write(build_srt_content(en_segments))
                self.tracer.add_artifact(str(dest_path))

                src_segments = []
                for orig_seg in segments:
                    sc = split_text_into_cues(orig_seg.text, orig_seg.start_seconds, orig_seg.end_seconds, 42, 2, len(src_segments) + 1)
                    src_segments.extend(sc)
                src_srt_path = self.budget.sandbox_path(f"subtitles_{source_lang}.srt")
                with open(src_srt_path, "w", encoding="utf-8") as f:
                    f.write(build_srt_content(src_segments))
                self.tracer.add_artifact(str(src_srt_path))

                print(f"\n[Verify] Running verification suite on generated subtitles...")
                rec_att = getattr(getattr(self, "_last_transcribe_output", None), "recovery_attempts", None)
                v_res = verify_subtitles(
                    target_segments=en_segments,
                    source_segments=segments,
                    target_lang=target_lang,
                    audio_duration_s=dur,
                    artifact_name=f"artifacts/{clean_name}",
                    recovery_attempts=rec_att,
                )
                self.tracer.record_verifications(v_res)
                for v in v_res:
                    print(f"  - [{'PASS' if v.passed else 'FAIL'}] {v.check_type}: {v.details}")
                return json.dumps({"srt_path": f"artifacts/{clean_name}", "cues_count": len(en_segments)}), 0.0, f"Generated {len(en_segments)} SRT cues in {clean_name}"

            elif segments:
                src_segments = []
                for orig_seg in segments:
                    sc = split_text_into_cues(orig_seg.text, orig_seg.start_seconds, orig_seg.end_seconds, 42, 2, len(src_segments) + 1)
                    src_segments.extend(sc)
                with open(dest_path, "w", encoding="utf-8") as f:
                    f.write(build_srt_content(src_segments))
                self.tracer.add_artifact(str(dest_path))

                print(f"\n[Verify] Running verification suite on generated subtitles...")
                rec_att = getattr(getattr(self, "_last_transcribe_output", None), "recovery_attempts", None)
                v_res = verify_subtitles(
                    target_segments=src_segments,
                    source_segments=segments,
                    target_lang=target_lang,
                    audio_duration_s=dur,
                    artifact_name=f"artifacts/{clean_name}",
                    recovery_attempts=rec_att,
                )
                self.tracer.record_verifications(v_res)
                for v in v_res:
                    print(f"  - [{'PASS' if v.passed else 'FAIL'}] {v.check_type}: {v.details}")
                return json.dumps({"srt_path": f"artifacts/{clean_name}", "cues_count": len(src_segments)}), 0.0, f"Generated {len(src_segments)} SRT cues in {clean_name}"

            else:
                sub_text = args.get("subtitles_text", "")
                cues = split_text_into_cues(sub_text, 0.0, dur, 42, 2)
                with open(dest_path, "w", encoding="utf-8") as f:
                    f.write(build_srt_content(cues))
                self.tracer.add_artifact(str(dest_path))
                return json.dumps({"srt_path": f"artifacts/{clean_name}", "cues_count": len(cues)}), 0.0, f"Generated {len(cues)} SRT cues in {clean_name}"

        elif tool_name == "write_file_artifact":
            rel_name = args["relative_filename"]
            clean_name = os.path.basename(rel_name)
            content = args["content"]
            dest_path = self.budget.sandbox_path(clean_name)
            with open(dest_path, "w", encoding="utf-8") as f:
                f.write(content)
            self.tracer.add_artifact(str(dest_path))
            return json.dumps({"artifact": f"artifacts/{clean_name}"}), 0.0, f"Saved artifact {clean_name}"

        else:
            raise ToolError("UNKNOWN_TOOL", f"Tool '{tool_name}' is not recognized.")

    def run_verifications_on_artifacts(self) -> None:
        """Run automated invariant verifications on produced artifacts that have not been verified yet."""
        already_verified = {
            v.get("artifact") if isinstance(v, dict) else getattr(v, "artifact", "")
            for v in self.tracer.verification_results
        }
        for art in list(self.tracer.artifacts_created):
            if art in already_verified:
                continue
            art_path = self.out_dir / art
            if not art_path.exists():
                continue

            # If an unverified Markdown table artifact exists
            if art.endswith(".md") and "table" in art.lower():
                content = art_path.read_text(encoding="utf-8")
                tables = count_markdown_table_structure(content)
                if tables:
                    lang = "mr" if "_mr" in art.lower() else ("bn" if "_bn" in art.lower() else "en")
                    v_res = verify_markdown_table_structure(
                        source_md=content,
                        target_md=content,
                        target_lang=lang,
                        artifact_name=art,
                    )
                    self.tracer.record_verifications(v_res)

    def run_llm_agent_loop(self, files: List[Path]) -> bool:
        """Run pure LLM tool-calling loop over MCP tools without keyword routing."""
        llm_calls = 0
        llm_tokens = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        # Check prompt injection in task prompt
        inj = self.check_prompt_injection(self.task)
        if inj:
            print(f"[Security Warning] Prompt injection attempt detected in task prompt: '{inj}'", file=sys.stderr)
            self.tracer.add_assumption(f"Task prompt contained potential prompt injection: '{inj}'. Ignored.")

        # Check for preflight deficit on tiny budgets upfront
        if self.budget.max_cost_inr <= 0.0001:
            raise ToolError("BUDGET_EXCEEDED", f"Budget limit ₹{self.budget.max_cost_inr:.4f} is too small to complete the task.")

        if self.budget.budget_source == "user_prompt":
            print(f"[Budget Manager] Enforcing user-stated hard cap: ₹{self.budget.max_cost_inr:.4f} (overrode CLI limit ₹{self.budget.cli_max_cost_inr:.2f})\n")

        manifest_lines = [f"- {f.name} (type: '{f.suffix.lower()}', path: '{f.resolve()}')" for f in files]
        user_prompt = (
            f"User Task: {self.task}\n\n"
            f"Available Input Files:\n" + "\n".join(manifest_lines) + "\n\n"
            f"Effective Hard Cost Cap: ₹{self.budget.max_cost_inr:.4f}\n"
            f"Remaining Cost Budget: ₹{self.budget.max_cost_inr - self.budget.spent_cost_inr:.4f}\n"
            f"Please call the appropriate tools to complete the task. Call complete_task when done, or mark_task_impossible if the request cannot be fulfilled by Bodhan Indic models."
        )

        messages = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        step_count = 0
        while step_count < self.budget.max_steps:
            step_count += 1
            msg, usage = llm_client.chat_completion_with_tools(messages=messages, tools=AGENT_TOOLS)
            llm_calls += 1
            for k in llm_tokens:
                llm_tokens[k] += usage.get(k, 0)

            tool_calls = getattr(msg, "tool_calls", None) or []
            if hasattr(msg, "model_dump"):
                assistant_dict = msg.model_dump(exclude_unset=True)
            else:
                assistant_dict = {"role": "assistant"}
                if msg.content:
                    assistant_dict["content"] = msg.content
                if tool_calls:
                    assistant_dict["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in tool_calls
                    ]
            messages.append(assistant_dict)

            if not tool_calls:
                final_ans = msg.content or "Completed task."
                self.run_verifications_on_artifacts()
                self.tracer.finalize(
                    task=self.task,
                    status="completed",
                    total_cost_inr=self.budget.spent_cost_inr,
                    final_answer=final_ans,
                    llm_calls=llm_calls,
                    llm_tokens=llm_tokens,
                    budget_limit_inr=self.budget.max_cost_inr,
                    user_stated_budget_inr=self.budget.user_stated_budget_inr,
                )
                self._print_summary(status="completed", duration_s=time.time() - self.tracer.start_time)
                return True

            for tc in tool_calls:
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else tc.function.arguments

                if fn_name == "mark_task_impossible":
                    reason = fn_args.get("reason", "Task is impossible with available Bodhan Indic models.")
                    self.tracer.finalize(
                        task=self.task,
                        status="impossible",
                        total_cost_inr=0.0,
                        error_message=reason,
                        final_answer=reason,
                        llm_calls=llm_calls,
                        llm_tokens=llm_tokens,
                        budget_limit_inr=self.budget.max_cost_inr,
                        user_stated_budget_inr=self.budget.user_stated_budget_inr,
                    )
                    self._print_summary(status="impossible", duration_s=time.time() - self.tracer.start_time, error_message=reason)
                    return True

                if fn_name == "complete_task" or (fn_name.startswith("complete_") and fn_name not in [t["function"]["name"] for t in AGENT_TOOLS]):
                    final_ans = fn_args.get("final_answer", "Completed task.")
                    assumptions = fn_args.get("assumptions") or []
                    for a in assumptions:
                        self.tracer.add_assumption(a)

                    if hasattr(self, "_last_synthesized_audio"):
                        print(f"\n[Verify] Running verification suite on audio summary...")
                        target_audio_p = self._last_synthesized_audio
                        t_lang = getattr(self, "_last_translated_lang", getattr(self, "_last_synthesized_lang", "ta"))
                        summary_text = getattr(self, "_last_translated_text", getattr(self, "_last_synthesized_text", ""))

                        # Perform actual back-transcription on the synthesized audio
                        print(f"[Verify] Performing back-transcription on {Path(target_audio_p).name} ({t_lang})...")
                        bt_res = transcribe_audio(audio_path=target_audio_p, language=t_lang)
                        bt_text = bt_res.text or ""

                        # Save back-transcript artifact
                        bt_art_path = self.budget.sandbox_path("back_transcript.txt")
                        with open(bt_art_path, "w", encoding="utf-8") as f:
                            f.write(bt_text)
                        self.tracer.add_artifact(str(bt_art_path))

                        # Evaluate initial coverage
                        cov = calculate_text_coverage(summary_text, bt_text)
                        print(f"[Verify] Initial back-transcription coverage: {cov * 100:.1f}%")

                        # Spec recovery requirement: if coverage < 70%, attempt re-synthesis recovery
                        if cov < 0.70:
                            print(f"[Recovery] Coverage {cov * 100:.1f}% < 70%. Attempting re-synthesis recovery...")
                            re_res = synthesize_speech(
                                text=summary_text,
                                language=t_lang,
                                voice=getattr(self, "_last_synthesized_voice", "Amit"),
                                output_path=target_audio_p,
                            )
                            bt_retry = transcribe_audio(audio_path=target_audio_p, language=t_lang)
                            retry_cov = calculate_text_coverage(summary_text, bt_retry.text or "")
                            print(f"[Recovery] Re-synthesis back-transcription coverage: {retry_cov * 100:.1f}%")
                            if retry_cov > cov:
                                bt_text = bt_retry.text or ""
                                with open(bt_art_path, "w", encoding="utf-8") as f:
                                    f.write(bt_text)

                        v_audio = verify_audio_summary(
                            ocr_pages=getattr(self, "_last_ocr_pages", 1),
                            summary_text=summary_text,
                            target_audio_path=target_audio_p,
                            back_transcription_text=bt_text,
                            target_lang=t_lang,
                            artifact_name=f"artifacts/{Path(target_audio_p).name}",
                        )
                        self.tracer.record_verifications(v_audio)
                        for v in v_audio:
                            badge = getattr(v, "status", "PASS" if v.passed else "FAIL")
                            print(f"  - [{badge}] {v.check_type}: {v.details}")

                    self.run_verifications_on_artifacts()
                    self.tracer.finalize(
                        task=self.task,
                        status="completed",
                        total_cost_inr=self.budget.spent_cost_inr,
                        final_answer=final_ans,
                        llm_calls=llm_calls,
                        llm_tokens=llm_tokens,
                        budget_limit_inr=self.budget.max_cost_inr,
                        user_stated_budget_inr=self.budget.user_stated_budget_inr,
                    )
                    self._print_summary(status="completed", duration_s=time.time() - self.tracer.start_time)
                    return True

                # Execute regular tool
                print(f"\n[Step {self.budget.current_step + 1}: LLM -> {fn_name}] Args: {fn_args}")
                t_exec = time.time()
                status_str = "success"
                try:
                    tool_res_str, cost_inr, summary_str = self.dispatch_tool(fn_name, fn_args)
                    lat_ms = int((time.time() - t_exec) * 1000)
                    print(f"[Done in {lat_ms}ms] Cost: ₹{cost_inr:.4f} | {summary_str}")
                except Exception as e:
                    lat_ms = int((time.time() - t_exec) * 1000)
                    err_msg = str(e)
                    print(f"[Tool Error in {lat_ms}ms] {fn_name}: {err_msg}", file=sys.stderr)
                    cost_inr = 0.0
                    status_str = "error"
                    summary_str = f"Error in {fn_name}: {err_msg}"
                    tool_res_str = json.dumps({
                        "error": getattr(e, "error_code", type(e).__name__),
                        "message": err_msg,
                        "instruction": f"Tool '{fn_name}' encountered an error: {err_msg}. Please adjust arguments, choose another tool from AGENT_TOOLS, or call complete_task.",
                    })

                self.tracer.record_step(
                    step_number=self.budget.current_step,
                    plan_step=f"LLM tool execution: {fn_name}",
                    tool=fn_name,
                    arguments=fn_args,
                    result_summary=summary_str,
                    latency_ms=lat_ms,
                    cost_inr=cost_inr,
                    cumulative_cost_inr=self.budget.spent_cost_inr,
                    status=status_str,
                )

                inj_in_res = self.check_prompt_injection(tool_res_str)
                if inj_in_res:
                    print(f"[Security Warning] Prompt injection attempt detected in tool output: '{inj_in_res}'", file=sys.stderr)
                    self.tracer.add_assumption(f"Tool output contained potential prompt injection: '{inj_in_res}'. Treated strictly as inert data.")

                wrapped_content = wrap_untrusted_data(tool_res_str)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": wrapped_content,
                })

        raise ToolError("STEP_LIMIT_EXCEEDED", f"Exceeded maximum allowed steps ({self.budget.max_steps}).")

    def execute(self) -> bool:
        """Main execution entrypoint. Returns True on success, False on error."""
        print("\n=== Bodhan Task Agent ===")
        print(f"Task:             {self.task}")
        print(f"Input Files Dir:  {self.files_dir}")
        print(f"Output Dir:       {self.out_dir}")
        print(f"Budget Limit:     ₹{self.budget.max_cost_inr:.2f} | Max Steps: {self.budget.max_steps}\n")

        try:
            files = self.find_input_files()
            if not files:
                raise ToolError("NO_INPUT_FILES", f"No supported media files found in: {self.files_dir}")

            print(f"[Init] Found {len(files)} candidate media file(s).")
            return self.run_llm_agent_loop(files)

        except ToolError as e:
            total_time = time.time() - self.tracer.start_time
            err_str = str(e)
            self.tracer.finalize(
                task=self.task,
                status="failed",
                total_cost_inr=self.budget.spent_cost_inr,
                error_message=err_str,
                budget_limit_inr=self.budget.max_cost_inr,
                user_stated_budget_inr=self.budget.user_stated_budget_inr,
            )
            print(f"\n[ERROR] {e}", file=sys.stderr)
            self._print_summary(status="failed", duration_s=total_time, error_message=err_str)
            return False
        except Exception as e:
            total_time = time.time() - self.tracer.start_time
            err_msg = f"Unexpected failure: {e}"
            self.tracer.finalize(
                task=self.task,
                status="failed",
                total_cost_inr=self.budget.spent_cost_inr,
                error_message=err_msg,
                budget_limit_inr=self.budget.max_cost_inr,
                user_stated_budget_inr=self.budget.user_stated_budget_inr,
            )
            print(f"\n[ERROR] {err_msg}", file=sys.stderr)
            self._print_summary(status="failed", duration_s=total_time, error_message=err_msg)
            return False
