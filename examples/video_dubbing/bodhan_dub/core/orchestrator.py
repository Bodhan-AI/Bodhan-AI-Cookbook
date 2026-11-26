import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bodhan_dub.agent.condenser import EnglishCondenser
from bodhan_dub.agent.cost_tracker import CostAndLatencyTracker
from bodhan_dub.agent.planner import PipelinePlanner
from bodhan_dub.agent.qa_verifier import QualityAssuranceVerifier
from bodhan_dub.clients.speak import BodhanSpeakClient
from bodhan_dub.clients.transcribe import BodhanTranscribeClient
from bodhan_dub.clients.translate import BodhanTranslateClient
from bodhan_dub.config import BodhanConfig
from bodhan_dub.core.audio_master import AudioMaster
from bodhan_dub.core.audio_sync import AudioSyncEngine
from bodhan_dub.core.subtitle_engine import SubtitleEngine
from bodhan_dub.core.video_engine import VideoEngine
from bodhan_dub.models.report import DubbingReport, LanguageExecutionSummary
from bodhan_dub.models.segment import DubSegment

logger = logging.getLogger(__name__)

class BodhanDubbingOrchestrator:
    """Central Agentic Coordinator for Bodhan Video Dubbing Pipeline."""

    def __init__(
        self,
        output_dir: Path,
        glossary: dict[str, str] | None = None,
        voices: dict[str, str] | None = None,
        code_mixed: bool = False,
        mock_mode: bool = False
    ):
        self.output_dir = Path(output_dir)
        self.glossary = glossary or {}
        self.voices = voices or {}
        self.code_mixed = code_mixed
        self.mock_mode = mock_mode

        # Initialize clients with config / environment keys
        self.transcribe_client = BodhanTranscribeClient(
            api_key=BodhanConfig.TRANSCRIBE_API_KEY,
            base_url=BodhanConfig.BASE_URL,
            mock_mode=mock_mode
        )
        self.translate_client = BodhanTranslateClient(
            api_key=BodhanConfig.TRANSLATE_API_KEY,
            base_url=BodhanConfig.BASE_URL,
            mock_mode=mock_mode
        )
        self.speak_client = BodhanSpeakClient(
            api_key=BodhanConfig.SPEAK_API_KEY,
            base_url=BodhanConfig.BASE_URL,
            voice_map=self.voices,
            mock_mode=mock_mode
        )

        # Core engines
        self.video_engine = VideoEngine()
        self.sync_engine = AudioSyncEngine()
        self.audio_master = AudioMaster()
        self.subtitle_engine = SubtitleEngine()

        # Agent components
        self.condenser = EnglishCondenser()
        self.qa_verifier = QualityAssuranceVerifier()
        self.cost_tracker = CostAndLatencyTracker()
        self.planner = PipelinePlanner(max_condensation_retries=BodhanConfig.MAX_CONDENSATION_RETRIES)

    def run(self, input_video: Path, target_languages: list[str]) -> DubbingReport:
        """Executes the full agentic multi-language dubbing workflow."""
        start_overall = time.time()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        work_dir = self.output_dir / ".work"
        work_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Step 1: Validating video integrity & extracting metadata: %s", input_video.name)
        video_meta = self.video_engine.validate_video(input_video)
        original_duration = video_meta["duration"]

        logger.info("Step 2: Extracting audio tracks...")
        speech_audio_path = work_dir / "extracted_speech_16k.wav"
        hq_audio_path = work_dir / "original_hq_48k.wav" if video_meta["has_audio"] else None
        self.video_engine.extract_audio(
            input_video,
            speech_audio_path,
            hq_audio_path,
            has_audio=video_meta["has_audio"],
            duration=original_duration
        )

        logger.info("Step 3: Transcribing audio using Bodhan Indic-Transcribe...")
        t_start = time.time()
        english_segments = self.transcribe_client.transcribe_segments(
            speech_audio_path,
            total_duration=original_duration,
            language="en"
        )
        self.cost_tracker.record_call("transcribe", time.time() - t_start)

        # Save out/transcript.en.json
        transcript_en_path = self.output_dir / "transcript.en.json"
        with open(transcript_en_path, "w", encoding="utf-8") as f:
            json.dump([s.model_dump() for s in english_segments], f, indent=2, ensure_ascii=False)
        logger.info("Saved English transcript: %s (%d segments)", transcript_en_path, len(english_segments))

        # Planned segments
        english_segments = self.planner.plan_segment_execution(english_segments)

        lang_summaries: dict[str, LanguageExecutionSummary] = {}

        # Step 4: Multi-Language Execution (with bounded concurrency)
        def _run_lang(lang: str) -> tuple[str, LanguageExecutionSummary]:
            lang_code = lang.strip().lower()
            lang_name = BodhanConfig.get_language_name(lang_code)
            logger.info("=== Processing language: %s (%s) ===", lang_name, lang_code)
            lang_dir = self.output_dir / lang_code
            lang_dir.mkdir(parents=True, exist_ok=True)
            lang_work_dir = work_dir / lang_code
            lang_work_dir.mkdir(parents=True, exist_ok=True)

            summary = self._process_language(
                lang_code=lang_code,
                lang_name=lang_name,
                segments=[s.model_copy(deep=True) for s in english_segments],
                lang_dir=lang_dir,
                lang_work_dir=lang_work_dir,
                input_video=input_video,
                hq_audio_path=hq_audio_path,
                original_duration=original_duration
            )
            return lang_code, summary

        max_workers = min(BodhanConfig.MAX_WORKERS, max(1, len(target_languages)))
        if max_workers > 1 and len(target_languages) > 1:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                for lang_code, summary in pool.map(_run_lang, target_languages):
                    lang_summaries[lang_code] = summary
        else:
            for lang in target_languages:
                lang_code, summary = _run_lang(lang)
                lang_summaries[lang_code] = summary

        # Step 5: Master Report Generation
        total_time = time.time() - start_overall
        out_dur = original_duration
        drift_ms = 0.0

        # Check output video duration if produced
        first_summary = next(iter(lang_summaries.values()), None)
        if first_summary and first_summary.output_video_path and Path(first_summary.output_video_path).exists():
            try:
                out_meta = self.video_engine.probe(Path(first_summary.output_video_path))
                out_dur = float(out_meta.get("format", {}).get("duration", original_duration))
                drift_ms = abs(out_dur - original_duration) * 1000.0
            except Exception as e:
                logger.warning("Could not probe output video duration: %s", e)

        report = DubbingReport(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            input_video=str(input_video),
            original_duration_seconds=round(original_duration, 3),
            output_duration_seconds=round(out_dur, 3),
            duration_difference_ms=round(drift_ms, 2),
            duration_within_spec=(drift_ms <= BodhanConfig.TIMELINE_TOLERANCE_MS),
            target_languages=target_languages,
            total_execution_time_seconds=round(total_time, 2),
            api_metrics=self.cost_tracker.get_summary(),
            languages=lang_summaries,
            overall_status="SUCCESS" if all(s.status == "SUCCESS" for s in lang_summaries.values()) else "PARTIAL",
            notes=[
                "Processed with Bodhan Indic-Transcribe, Indic-Translate, and Indic-Speak.",
                f"Video timeline duration tolerance: ±{BodhanConfig.TIMELINE_TOLERANCE_MS}ms.",
                f"Segment start alignment tolerance: ±{BodhanConfig.SEGMENT_ALIGNMENT_TOLERANCE_MS}ms."
            ]
        )

        report_path = self.output_dir / "report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report.model_dump_json(indent=2))
        logger.info("Generated master report: %s", report_path)

        return report

    def _process_language(
        self,
        lang_code: str,
        lang_name: str,
        segments: list[DubSegment],
        lang_dir: Path,
        lang_work_dir: Path,
        input_video: Path,
        hq_audio_path: Path | None,
        original_duration: float
    ) -> LanguageExecutionSummary:
        """Processes one target language completely through translation, TTS, sync, QA, mixing and muxing."""
        tts_dir = lang_work_dir / "tts"
        fitted_dir = lang_work_dir / "fitted"
        tts_dir.mkdir(parents=True, exist_ok=True)
        fitted_dir.mkdir(parents=True, exist_ok=True)

        accepted_count = 0
        condensed_count = 0
        manual_review_count = 0
        cers: list[float] = []
        tempos: list[float] = []
        errors: list[str] = []
        warnings: list[str] = []

        for seg in segments:
            seg.target_language = lang_code
            source_for_translation = seg.source_text

            # 1. Bodhan Indic-Translate
            t_trans = time.time()
            translated = self.translate_client.translate(
                text=source_for_translation,
                target_lang=lang_code,
                source_lang="en",
                glossary=self.glossary,
                code_mixed=self.code_mixed
            )
            self.cost_tracker.record_call("translate", time.time() - t_trans)
            seg.translated_text = translated

            # 2. Bodhan Indic-Speak
            t_speak = time.time()
            tts_out = tts_dir / f"{seg.id}_raw.wav"
            self.speak_client.generate_speech(
                text=seg.translated_text,
                target_lang=lang_code,
                output_path=tts_out,
                target_duration=seg.target_duration
            )
            self.cost_tracker.record_call("speak", time.time() - t_speak)
            seg.generated_audio_path = str(tts_out)

            # 3. Audio duration analysis and Synchronization Decisions
            fits, required_tempo, fitted_wav = self.sync_engine.evaluate_and_fit_segment(
                seg, tts_out, fitted_dir
            )

            # 4. Agentic Condensation Decision Loop
            if not fits and self.planner.should_condense(seg):
                logger.info("Agent: Segment %s overlong (tempo=%.2fx). Triggering English condensation loop.",
                            seg.id, required_tempo)
                condensed_count += 1
                attempts = seg.metadata.get("condensation_attempts", 0) + 1
                seg.metadata["condensation_attempts"] = attempts

                # Condense English source text
                condensed_en = self.condenser.condense(seg.source_text, target_ratio=0.80)
                seg.condensed_text = condensed_en

                # Re-translate via Bodhan Indic-Translate
                t_re_trans = time.time()
                re_translated = self.translate_client.translate(
                    text=condensed_en,
                    target_lang=lang_code,
                    source_lang="en",
                    glossary=self.glossary,
                    code_mixed=self.code_mixed
                )
                self.cost_tracker.record_call("translate", time.time() - t_re_trans)
                seg.translated_text = re_translated

                # Re-synthesize with Bodhan Indic-Speak
                t_re_speak = time.time()
                tts_re_out = tts_dir / f"{seg.id}_re_raw.wav"
                self.speak_client.generate_speech(
                    text=re_translated,
                    target_lang=lang_code,
                    output_path=tts_re_out,
                    target_duration=seg.target_duration
                )
                self.cost_tracker.record_call("speak", time.time() - t_re_speak)
                seg.generated_audio_path = str(tts_re_out)

                # Re-evaluate audio fit
                fits, required_tempo, fitted_wav = self.sync_engine.evaluate_and_fit_segment(
                    seg, tts_re_out, fitted_dir
                )

            tempos.append(seg.applied_tempo_factor)

            # 5. Back-transcription using Bodhan Indic-Transcribe
            t_back = time.time()
            back_resp = self.transcribe_client.transcribe(
                audio_source=Path(seg.fitted_audio_path or seg.generated_audio_path),
                language=lang_code
            )
            self.cost_tracker.record_call("back_transcribe", time.time() - t_back)
            back_text = back_resp.get("text", "").strip()

            # 6. Quality Assurance Verification
            seg = self.qa_verifier.verify_segment(
                segment=seg,
                audio_path=Path(seg.fitted_audio_path or seg.generated_audio_path),
                back_transcription=back_text
            )

            if seg.cer is not None:
                cers.append(seg.cer)

            if seg.qa_status == "ACCEPTED":
                accepted_count += 1
            else:
                manual_review_count += 1
                warnings.append(f"Segment {seg.id} ({seg.qa_status}): {'; '.join(seg.qa_reasons)}")

        # 7. Assemble Master Dubbed Speech Timeline
        speech_timeline_wav = lang_work_dir / "timeline_speech.wav"
        self.sync_engine.assemble_timeline(
            segments=segments,
            total_video_duration=original_duration,
            output_timeline_path=speech_timeline_wav
        )

        # 8. Background Audio Mixing & Ducking
        master_audio_wav = lang_work_dir / f"master_audio_{lang_code}.wav"
        self.audio_master.mix_and_master(
            dubbed_speech_wav=speech_timeline_wav,
            original_hq_audio=hq_audio_path,
            output_master_wav=master_audio_wav
        )

        # 9. Subtitle Generation
        subtitles_srt = lang_dir / f"subtitles.{lang_code}.srt"
        subtitles_vtt = lang_dir / f"subtitles.{lang_code}.vtt"
        self.subtitle_engine.generate_srt(segments, subtitles_srt)
        self.subtitle_engine.generate_vtt(segments, subtitles_vtt)

        # 10. Video Remuxing
        output_video_path = lang_dir / f"video.{lang_code}.mp4"
        self.video_engine.mux_video(
            video_path=input_video,
            audio_path=master_audio_wav,
            output_path=output_video_path,
            subtitle_path=subtitles_srt,
            language_code=lang_code,
            target_duration=original_duration
        )

        # 11. Save segments JSON
        segments_json_path = lang_dir / f"segments.{lang_code}.json"
        with open(segments_json_path, "w", encoding="utf-8") as f:
            json.dump([s.model_dump() for s in segments], f, indent=2, ensure_ascii=False)

        avg_cer = round(sum(cers) / max(1, len(cers)), 4) if cers else None
        avg_tempo = round(sum(tempos) / max(1, len(tempos)), 3) if tempos else 1.0

        return LanguageExecutionSummary(
            language=lang_code,
            language_name=lang_name,
            status="SUCCESS",
            segment_count=len(segments),
            accepted_segments=accepted_count,
            condensed_segments=condensed_count,
            manual_review_segments=manual_review_count,
            average_cer=avg_cer,
            average_tempo_factor=avg_tempo,
            min_tempo_factor=min(tempos) if tempos else 1.0,
            max_tempo_factor=max(tempos) if tempos else 1.0,
            output_video_path=str(output_video_path),
            subtitles_srt_path=str(subtitles_srt),
            subtitles_vtt_path=str(subtitles_vtt),
            segments_json_path=str(segments_json_path),
            errors=errors,
            warnings=warnings
        )
