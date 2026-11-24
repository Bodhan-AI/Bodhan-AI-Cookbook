"""
Duration Fit Controller and Per-Segment Lifecycle Coordinator.
Coordinates TTS synthesis, QA evaluation, retry planning, LLM English condensation,
slack borrowing, and pitch-preserving time stretching (atempo).
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from bodhan_dub.gateway.bodhan import BodhanClient
from bodhan_dub.gateway.cost import BudgetExceededError
from bodhan_dub.gateway.llm import LLMClient
from bodhan_dub.gateway.resilience import CircuitBreakerOpen
from bodhan_dub.languages import LanguageRegistry
from bodhan_dub.logging import PipelineLogger
from bodhan_dub.media.audio_proc import time_stretch_audio
from bodhan_dub.policy import guard_llm_output_english_only
from bodhan_dub.stages.qa import (
    QAMetrics,
    SpeechQA,
    SynthesisCandidate,
    determine_retry_action,
)
from bodhan_dub.stages.translate import check_dominant_script
from bodhan_dub.stages.tts import CPSCalibrator, TTSSynthesizer
from bodhan_dub.text.spans import verify_numbers_preserved


class CondensedEnglish(BaseModel):
    condensed_en: str = Field(
        default="",
        description="Concise English sentence preserving all entities and numbers.",
    )


class ParaphrasedEnglish(BaseModel):
    paraphrased_en: str = Field(
        default="",
        description="Simplified English paraphrase preserving all entities and numbers.",
    )


class SegmentFitResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    segment_id: str
    source_en: str
    final_en: str
    final_translation: str
    raw_duration: float
    final_duration: float
    slot_duration: float
    start_shift: float
    speed_factor: float
    qa_metrics: QAMetrics
    qa_status: str  # passed, degraded, skipped
    fit_status: str  # ok, stretched, borrowed_slack, overrun, skipped
    attempts: int
    reasons: List[str] = Field(default_factory=list)
    raw_audio: Optional[Any] = Field(default=None, exclude=True)


class SegmentFitController:
    """
    Implements the Per-Segment Duration Fit & QA loop (Diagram 3).
    Ensures speech fits within the available slot and surrounding slacks without
    exceeding 1.2x time stretch, truncating speech, or introducing overlaps.
    """

    def __init__(
        self,
        bodhan_client: Optional[BodhanClient],
        llm_client: Optional[LLMClient],
        logger: PipelineLogger,
        tts_synthesizer: Optional[TTSSynthesizer] = None,
        speech_qa: Optional[SpeechQA] = None,
        calibrator: Optional[CPSCalibrator] = None,
        registry: Optional[LanguageRegistry] = None,
        settings: Optional[Any] = None,
        cer_warn: Optional[float] = None,
        cer_fail: Optional[float] = None,
        cer_degraded_max: Optional[float] = None,
        max_latin_share: Optional[float] = None,
    ):
        self.bodhan_client = bodhan_client
        self.llm_client = llm_client
        self.logger = logger
        self.registry = registry
        self.settings = settings
        self.cer_warn = cer_warn if cer_warn is not None else (getattr(settings, "cer_warn", 0.15) if settings else 0.15)
        self.cer_fail = cer_fail if cer_fail is not None else (getattr(settings, "cer_fail", 0.30) if settings else 0.30)
        self.cer_degraded_max = cer_degraded_max if cer_degraded_max is not None else (getattr(settings, "cer_degraded_max", 0.45) if settings else 0.45)
        self.max_latin_share = max_latin_share if max_latin_share is not None else (getattr(settings, "max_latin_share", 0.30) if settings else 0.30)

        self.calibrator = calibrator or CPSCalibrator()
        self.synthesizer = tts_synthesizer or (
            TTSSynthesizer(
                bodhan_client=bodhan_client,
                logger=logger,
                calibrator=self.calibrator,
            )
            if bodhan_client is not None
            else None
        )
        self.speech_qa = speech_qa or (
            SpeechQA(
                bodhan_client=bodhan_client,
                logger=logger,
                cer_warn=self.cer_warn,
                cer_fail=self.cer_fail,
                cer_degraded_max=self.cer_degraded_max,
                max_latin_share=self.max_latin_share,
            )
            if bodhan_client is not None
            else None
        )

    async def _condense_english_source(
        self,
        source_en: str,
        target_char_budget: int,
        lang: str,
        segment_id: str,
        terms_to_protect: Optional[List[str]] = None,
    ) -> Optional[str]:
        """
        Uses LLM to condense English source text within a character budget.
        Validates English-only output, numbers, and proper names / glossary terms.
        """
        if not self.llm_client:
            return None

        try:
            condensed = await self.llm_client.condense_english(
                source_en=source_en,
                target_char_budget=target_char_budget,
                lang=lang,
                segment_id=segment_id,
            )
            if not condensed:
                return None

            # Policy Guard: reject any non-English/Indic characters
            guard_llm_output_english_only(condensed)

            # Preserve numbers deterministically
            if not verify_numbers_preserved(source_en, condensed):
                self.logger.warning(
                    "Condensed English omitted numbers; discarding condensation",
                    stage="condense_fit",
                    lang=lang,
                    segment_id=segment_id,
                )
                return None

            # Preserve glossary terms / proper names deterministically
            if terms_to_protect:
                for term in terms_to_protect:
                    if term.lower() not in condensed.lower():
                        self.logger.warning(
                            f"Condensed English dropped term '{term}'; discarding condensation",
                            stage="condense_fit",
                            lang=lang,
                            segment_id=segment_id,
                        )
                        return None

            return condensed
        except (BudgetExceededError, CircuitBreakerOpen):
            raise
        except Exception as e:
            self.logger.warning(
                f"LLM condensation failed or was rejected: {e}",
                stage="condense_fit",
                lang=lang,
                segment_id=segment_id,
            )
            return None

    async def _paraphrase_english_source(
        self,
        source_en: str,
        lang: str,
        segment_id: str,
        terms_to_protect: Optional[List[str]] = None,
    ) -> Optional[str]:
        """
        Uses LLM to paraphrase English source text for cleaner translation and synthesis.
        """
        if not self.llm_client:
            return None

        try:
            paraphrased = await self.llm_client.paraphrase_english(
                source_en=source_en,
                lang=lang,
                segment_id=segment_id,
            )
            if not paraphrased:
                return None

            guard_llm_output_english_only(paraphrased)

            if not verify_numbers_preserved(source_en, paraphrased):
                return None

            if terms_to_protect:
                for term in terms_to_protect:
                    if term.lower() not in paraphrased.lower():
                        return None

            return paraphrased
        except (BudgetExceededError, CircuitBreakerOpen):
            raise
        except Exception as e:
            self.logger.warning(
                f"LLM paraphrase failed: {e}",
                stage="paraphrase_qa",
                lang=lang,
                segment_id=segment_id,
            )
            return None

    async def process_segment(
        self,
        segment_id: str,
        source_en: str,
        initial_translation: str,
        slot_duration: float,
        slack_before: float,
        slack_after: float,
        lang: str,
        voice: str,
        code_mixed: bool = False,
        terms_to_protect: Optional[List[str]] = None,
    ) -> Tuple[SegmentFitResult, Optional[np.ndarray]]:
        """
        Executes Diagram 3 Per-segment duration fit and QA loop.
        Returns (result_metadata, audio_48k_samples).
        """
        current_en = source_en
        current_tr = initial_translation.strip()
        target_code = lang
        if code_mixed and self.registry:
            info = self.registry.get_info(lang)
            if info and info.code_mixed_target:
                target_code = info.code_mixed_target

        # Check if initial translation is missing or purely Latin/English
        is_script_valid = False
        effective_max_latin = 0.70 if code_mixed else self.max_latin_share
        if current_tr:
            if self.registry:
                is_script_valid, _ = check_dominant_script(
                    current_tr, lang, self.registry, max_latin_share=effective_max_latin
                )
            else:
                is_script_valid = True

        if not current_tr or not is_script_valid:
            self.logger.warning(
                f"Segment {segment_id} has invalid/empty translation ('{current_tr}'); skipping TTS to prevent sending English to Indic voice",
                stage="fit",
                lang=lang,
                segment_id=segment_id,
            )
            result = SegmentFitResult(
                segment_id=segment_id,
                source_en=source_en,
                final_en=current_en,
                final_translation="",
                raw_duration=0.0,
                final_duration=0.0,
                slot_duration=slot_duration,
                start_shift=0.0,
                speed_factor=1.0,
                qa_metrics=QAMetrics(
                    cer=1.0,
                    passed=False,
                    is_empty=True,
                    failure_reasons=["translation_failed_or_non_indic"],
                ),
                qa_status="skipped",
                fit_status="skipped",
                attempts=0,
                reasons=["translation_failed_or_non_indic"],
            )
            return result, None

        predicted_dur = self.calibrator.predict_duration(current_tr, lang, voice)
        pred_ratio = predicted_dur / max(0.1, slot_duration)

        # 1. Check if pre-synthesis condensation is needed (pred_ratio > 1.3)
        if pred_ratio > 1.3:
            char_budget = max(15, int(len(source_en) * (slot_duration / predicted_dur) * 0.95))
            condensed = await self._condense_english_source(
                source_en, char_budget, lang, segment_id, terms_to_protect
            )
            if condensed and self.bodhan_client:
                current_en = condensed
                # Re-translate via Indic-Translate
                try:
                    re_tr_text, _ = await self.bodhan_client.translate(
                        text=current_en,
                        target_language_code=target_code,
                        source_language_code="en",
                        stage="re_translate_pre_fit",
                        lang=lang,
                        segment_id=segment_id,
                    )
                    if re_tr_text and re_tr_text.strip():
                        if not self.registry or check_dominant_script(
                            re_tr_text, lang, self.registry, max_latin_share=effective_max_latin
                        )[0]:
                            current_tr = re_tr_text.strip()
                except (BudgetExceededError, CircuitBreakerOpen):
                    raise
                except Exception as e:
                    self.logger.warning(
                        f"Re-translation failed: {e}", stage="fit", lang=lang, segment_id=segment_id
                    )

        # 2. Synthesis and QA loop (max 3 attempts)
        attempts = 0
        candidates: List[SynthesisCandidate] = []
        chosen_candidate: Optional[SynthesisCandidate] = None
        current_max_chars = 180
        last_nonce = None

        while attempts < 3:
            attempts += 1
            try:
                if self.synthesizer:
                    synth_kwargs = {}
                    if last_nonce is not None:
                        synth_kwargs["nonce"] = last_nonce
                    if current_max_chars != 180:
                        synth_kwargs["max_chars"] = current_max_chars
                    audio_48k, actual_dur, p_dur = await self.synthesizer.synthesize(
                        text=current_tr,
                        lang=lang,
                        voice=voice,
                        segment_id=f"{segment_id}_a{attempts}",
                        **synth_kwargs,
                    )
                else:
                    audio_48k = np.array([], dtype=np.float32)
                    actual_dur = 0.0
                    p_dur = predicted_dur
            except (BudgetExceededError, CircuitBreakerOpen):
                raise
            except Exception as e:
                self.logger.error(
                    f"TTS synthesis exception on attempt {attempts}: {e}",
                    stage="tts",
                    lang=lang,
                    segment_id=segment_id,
                )
                # A5: When a synth exception occurs, the next attempt must use a nonce
                last_nonce = attempts + 1
                audio_48k = np.array([], dtype=np.float32)
                actual_dur = 0.0
                p_dur = predicted_dur

            # Evaluate QA
            is_warmed_up = (
                self.synthesizer.calibrator.is_warmed_up(lang, voice)
                if (self.synthesizer and hasattr(self.synthesizer, "calibrator"))
                else True
            )
            eval_kwargs = {}
            import inspect
            if hasattr(self.speech_qa, "evaluate") and "is_warmed_up" in inspect.signature(self.speech_qa.evaluate).parameters:
                eval_kwargs["is_warmed_up"] = is_warmed_up

            if self.speech_qa and len(audio_48k) > 0:
                qa_metrics = await self.speech_qa.evaluate(
                    audio_48k=audio_48k,
                    reference_text=current_tr,
                    predicted_duration=p_dur,
                    lang=lang,
                    segment_id=f"{segment_id}_a{attempts}",
                    **eval_kwargs,
                )
            else:
                qa_metrics = QAMetrics(
                    cer=0.0 if len(audio_48k) > 0 else 1.0,
                    passed=(len(audio_48k) > 0),
                    has_repetition=False,
                    has_tail_omission=False,
                    duration_ratio=1.0,
                    is_silent=False,
                    is_clipped=False,
                    is_empty=len(audio_48k) == 0,
                    failure_reasons=[] if len(audio_48k) > 0 else ["empty_audio"],
                )

            cand = SynthesisCandidate(
                attempt=attempts,
                text=current_tr,
                audio_48k=audio_48k,
                duration_s=actual_dur,
                metrics=qa_metrics,
            )
            candidates.append(cand)

            if qa_metrics.passed:
                chosen_candidate = cand
                break

            if attempts < 3:
                previous_actions = [getattr(c, "action", "") for c in candidates if getattr(c, "action", None)]
                deterministic_action = determine_retry_action(qa_metrics.failure_reasons, attempts, previous_actions)
                if self.llm_client and getattr(getattr(self.llm_client, "settings", None), "llm_api_key", None):
                    try:
                        llm_choice = await self.llm_client.plan_retry_action(
                            failure_reasons=qa_metrics.failure_reasons,
                            previous_actions=previous_actions,
                            lang=lang,
                            segment_id=segment_id,
                        )
                        valid_choices = ["split_text", "paraphrase_source", "condense", "retry_same"]
                        if llm_choice in valid_choices and llm_choice not in previous_actions:
                            action = llm_choice
                        else:
                            action = deterministic_action
                    except Exception:
                        action = deterministic_action
                else:
                    action = deterministic_action
                cand.action = action
                self.logger.info(
                    f"QA failed on attempt {attempts} (reasons: {qa_metrics.failure_reasons}); planning action: {action}",
                    stage="qa",
                    lang=lang,
                    segment_id=segment_id,
                )

                if action == "split_text":
                    # Re-split at a smaller safe length (half previous max_chars)
                    current_max_chars = max(30, current_max_chars // 2)
                    last_nonce = attempts + 1
                elif action == "paraphrase_source":
                    paraphrased = await self._paraphrase_english_source(
                        source_en, lang, segment_id, terms_to_protect
                    )
                    if paraphrased and self.bodhan_client:
                        current_en = paraphrased
                        try:
                            tr_text, _ = await self.bodhan_client.translate(
                                text=current_en,
                                target_language_code=target_code,
                                source_language_code="en",
                                stage="re_translate_paraphrase",
                                lang=lang,
                                segment_id=segment_id,
                            )
                            if tr_text and tr_text.strip():
                                if not self.registry or check_dominant_script(
                                    tr_text, lang, self.registry, max_latin_share=effective_max_latin
                                )[0]:
                                    current_tr = tr_text.strip()
                        except (BudgetExceededError, CircuitBreakerOpen):
                            raise
                        except Exception as e:
                            self.logger.warning(
                                f"Re-translation failed: {e}",
                                stage="fit",
                                lang=lang,
                                segment_id=segment_id,
                            )
                    last_nonce = None
                elif action == "condense":
                    budget = max(15, int(len(current_en) * 0.8))
                    condensed = await self._condense_english_source(
                        current_en, budget, lang, segment_id, terms_to_protect
                    )
                    if condensed and self.bodhan_client:
                        current_en = condensed
                        try:
                            tr_text, _ = await self.bodhan_client.translate(
                                text=current_en,
                                target_language_code=target_code,
                                source_language_code="en",
                                stage="re_translate_condense",
                                lang=lang,
                                segment_id=segment_id,
                            )
                            if tr_text and tr_text.strip():
                                if not self.registry or check_dominant_script(
                                    tr_text, lang, self.registry, max_latin_share=effective_max_latin
                                )[0]:
                                    current_tr = tr_text.strip()
                        except (BudgetExceededError, CircuitBreakerOpen):
                            raise
                        except Exception as e:
                            self.logger.warning(
                                f"Re-translation failed: {e}",
                                stage="fit",
                                lang=lang,
                                segment_id=segment_id,
                            )
                    last_nonce = None
                else:  # retry_same
                    last_nonce = attempts + 1

        # 3. Decision on candidate Usability (Diagram 3: O2)
        qa_status = "passed"
        if chosen_candidate is None:
            # Sort remaining candidates by lowest CER (best candidate by CER is kept)
            valid_cands = [c for c in candidates if len(c.audio_48k) > 0 and not c.metrics.is_empty]
            if valid_cands:
                valid_cands.sort(key=lambda c: c.metrics.cer)
                best = valid_cands[0]
                if (
                    best.metrics.cer is not None
                    and best.metrics.cer < self.cer_degraded_max
                    and not best.metrics.is_silent
                ):
                    chosen_candidate = best
                    qa_status = "degraded"
                else:
                    qa_status = "skipped"
            else:
                qa_status = "skipped"

        # If candidate is unusable / skipped, original audio is kept for this slot
        if chosen_candidate is None or qa_status == "skipped":
            last_metrics = (
                candidates[-1].metrics
                if candidates
                else QAMetrics(
                    cer=1.0, passed=False, is_empty=True, failure_reasons=["all_attempts_failed"]
                )
            )
            result = SegmentFitResult(
                segment_id=segment_id,
                source_en=source_en,
                final_en=current_en,
                final_translation=current_tr,
                raw_duration=0.0,
                final_duration=0.0,
                slot_duration=slot_duration,
                start_shift=0.0,
                speed_factor=1.0,
                qa_metrics=last_metrics,
                qa_status="skipped",
                fit_status="skipped",
                attempts=attempts,
                reasons=["unusable_synthesis_skipped_for_original_audio"],
            )
            return result, None

        # 4. Duration Fit Strategy (Diagram 3: P, Q, R, S, T)
        audio = chosen_candidate.audio_48k
        raw_dur = len(audio) / 48000.0
        borrow_before = min(0.250, max(0.0, slack_before))
        borrow_after = max(0.0, slack_after)
        effective_slot = slot_duration + borrow_before + borrow_after

        # B7: Post-synthesis condensation loop if audio exceeds available slot by > 1.20x
        condense_passes = 0
        while raw_dur > (effective_slot * 1.20) and condense_passes < 2:
            condense_passes += 1
            target_budget = max(15, int(len(current_en) * ((effective_slot * 1.15) / raw_dur)))
            condensed = await self._condense_english_source(
                current_en, target_budget, lang, segment_id, terms_to_protect
            )
            if not condensed or not self.bodhan_client or not self.synthesizer:
                break
            try:
                # Re-translate condensed English
                re_tr, _ = await self.bodhan_client.translate(
                    text=condensed,
                    target_language_code=target_code,
                    source_language_code="en",
                    stage="re_translate_fit_condense",
                    lang=lang,
                    segment_id=segment_id,
                )
                if re_tr and re_tr.strip():
                    if not self.registry or check_dominant_script(re_tr, lang, self.registry)[0]:
                        # Re-synthesize
                        re_audio, re_dur, _ = await self.synthesizer.synthesize(
                            text=re_tr.strip(),
                            lang=lang,
                            voice=voice,
                            segment_id=f"{segment_id}_condense{condense_passes}",
                        )
                        if len(re_audio) > 0:
                            # A3: Evaluate QA on the new audio
                            if self.speech_qa:
                                re_qa = await self.speech_qa.evaluate(
                                    audio_48k=re_audio,
                                    reference_text=re_tr.strip(),
                                    predicted_duration=re_dur,
                                    lang=lang,
                                    segment_id=f"{segment_id}_condense{condense_passes}",
                                    is_warmed_up=True,
                                )
                            else:
                                re_qa = QAMetrics(cer=0.0, passed=True, failure_reasons=[])

                            # Accept new candidate ONLY if:
                            # 1) actually shorter than current audio (re_dur < raw_dur)
                            # AND 2) passes QA or has a lower CER than current candidate
                            current_cer = (
                                chosen_candidate.metrics.cer
                                if (chosen_candidate and chosen_candidate.metrics)
                                else 1.0
                            )
                            is_cer_improved = (
                                re_qa.cer is not None
                                and current_cer is not None
                                and re_qa.cer < current_cer
                            )

                            if re_dur < raw_dur and (re_qa.passed or is_cer_improved):
                                current_en = condensed
                                current_tr = re_tr.strip()
                                audio = re_audio
                                raw_dur = re_dur
                                chosen_candidate = SynthesisCandidate(
                                    attempt=chosen_candidate.attempt if chosen_candidate else 1,
                                    text=current_tr,
                                    audio_48k=audio,
                                    duration_s=raw_dur,
                                    metrics=re_qa,
                                )
                            else:
                                self.logger.info(
                                    f"Condense candidate rejected (re_dur={re_dur:.2f}s vs curr={raw_dur:.2f}s, "
                                    f"passed={re_qa.passed}, cer={re_qa.cer} vs curr={current_cer})",
                                    stage="fit",
                                    lang=lang,
                                    segment_id=segment_id,
                                )
            except (BudgetExceededError, CircuitBreakerOpen):
                raise
            except Exception as e:
                self.logger.warning(
                    f"Post-synthesis condensation pass {condense_passes} failed: {e}",
                    stage="fit",
                    lang=lang,
                    segment_id=segment_id,
                )
                break

        fit_ratio = raw_dur / max(0.1, slot_duration)

        if fit_ratio <= 1.0:
            # Natural speed, pad with silence if needed in scheduler
            final_audio = audio
            final_dur = raw_dur
            speed_factor = 1.0
            start_shift = 0.0
            fit_status = "ok"
        elif fit_ratio <= 1.20:
            # Stretch with atempo
            speed_factor = round(fit_ratio, 3)
            final_audio = time_stretch_audio(audio, speed_factor, sample_rate=48000)
            final_dur = len(final_audio) / 48000.0
            start_shift = 0.0
            fit_status = "stretched"
        else:
            # fit_ratio > 1.20: borrow slack, stretch to max 1.20, flag overrun if still over
            eff_ratio = raw_dur / max(0.1, effective_slot)

            if eff_ratio <= 1.20:
                speed_factor = round(max(1.0, eff_ratio), 3)
                final_audio = time_stretch_audio(audio, speed_factor, sample_rate=48000)
                final_dur = len(final_audio) / 48000.0
                excess = max(0.0, final_dur - slot_duration)
                shift = min(borrow_before, excess)
                start_shift = -round(shift, 3)
                fit_status = "borrowed_slack"
            else:
                # Cap speed factor at 1.20 max (Hard Constraint: never speed up beyond 1.20)
                speed_factor = 1.20
                final_audio = time_stretch_audio(audio, speed_factor, sample_rate=48000)
                final_dur = len(final_audio) / 48000.0
                excess = max(0.0, final_dur - slot_duration)
                shift = min(borrow_before, excess)
                start_shift = -round(shift, 3)
                fit_status = "overrun"

        result = SegmentFitResult(
            segment_id=segment_id,
            source_en=source_en,
            final_en=current_en,
            final_translation=current_tr,
            raw_duration=round(raw_dur, 3),
            final_duration=round(final_dur, 3),
            slot_duration=round(slot_duration, 3),
            start_shift=start_shift,
            speed_factor=speed_factor,
            qa_metrics=chosen_candidate.metrics,
            qa_status=qa_status,
            fit_status=fit_status,
            attempts=attempts,
            reasons=chosen_candidate.metrics.failure_reasons,
            raw_audio=audio.copy(),
        )

        return result, final_audio
