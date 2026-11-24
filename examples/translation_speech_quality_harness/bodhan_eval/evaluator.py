import json
import logging
from pathlib import Path

from bodhan_eval.translation_checks import TranslationQualityChecker
from bodhan_eval.back_translation import BackTranslationChecker
from bodhan_eval.speech_checks import SpeechQualityChecker
from bodhan_eval.llm_judge import LLMJudge
from bodhan_eval.bodhan_client import BodhanClient


logger = logging.getLogger("indiceval")


class IndicEvalEvaluator:
    """
    Main evaluation orchestrator.

    Components:
        1. Deterministic translation checks
        2. Back-translation evaluation
        3. Speech evaluation
        4. LLM-based translation judging

    Separates translation-quality failures from
    evaluation and infrastructure failures.
    """

    def __init__(
        self,
        run_back_translation=False,
        run_llm_judge=False,
        run_speech=True,
        bodhan_client=None,
        llm_judge=None
    ):

        self.client = bodhan_client or BodhanClient()

        self.translation_checker = TranslationQualityChecker()

        self.back_translation_checker = (
            BackTranslationChecker(self.client)
            if run_back_translation else None
        )

        self.speech_checker = (
            SpeechQualityChecker()
            if run_speech else None
        )

        self.llm_judge = (
            (llm_judge or LLMJudge())
            if run_llm_judge else None
        )

    # =========================================================
    # DATASET LOADING
    # =========================================================

    @staticmethod
    def load_dataset(dataset_path):
        """Load and validate a JSONL dataset."""

        records = []

        with open(dataset_path, "r", encoding="utf-8") as file:

            for line_number, line in enumerate(file, start=1):

                if not line.strip():
                    continue

                try:
                    record = json.loads(line)

                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"Invalid JSON on line {line_number}: {exc}"
                    ) from exc

                if not isinstance(record, dict):
                    raise ValueError(
                        f"Expected a JSON object on line {line_number}"
                    )

                required_fields = ["id", "source", "lang"]

                for field in required_fields:

                    if field not in record:
                        raise ValueError(
                            f"Missing '{field}' on line {line_number}"
                        )

                records.append(record)

        return records

    # =========================================================
    # STATUS MANAGEMENT
    # =========================================================

    @staticmethod
    def update_status(result, new_status):
        """
        Update translation quality status.

        Priority:
            failed > warning > passed

        The status field is retained for backward compatibility.
        """

        priority = {
            "passed": 0,
            "warning": 1,
            "failed": 2
        }

        current_status = result["translation_status"]

        if priority.get(new_status, 0) > priority.get(
            current_status, 0
        ):

            result["translation_status"] = new_status

            # Backward compatibility with existing CLI reports.
            result["status"] = new_status

    @staticmethod
    def record_evaluation_error(
        result,
        component,
        error
    ):
        """
        Record an infrastructure or evaluation failure.

        These errors must not automatically become
        translation-quality failures.
        """

        result["evaluation_errors"].append({
            "component": component,
            "category": "evaluation_error",
            "message": str(error)
        })

        result["evaluation_status"] = "partial"

        logger.error(
            "%s evaluation failed for %s: %s",
            component,
            result["id"],
            error
        )

    # =========================================================
    # SPEECH EVALUATION
    # =========================================================

    def evaluate_speech(
        self,
        result,
        translation,
        audio_path
    ):
        """
        Evaluate speech independently.

        Missing audio is skipped.
        API failures are recorded separately.
        """

        if not audio_path:

            result["checks"]["speech"] = {
                "status": "skipped",
                "reason": "No audio file provided."
            }

            result["speech_status"] = "skipped"

            return

        try:

            audio_file = Path(audio_path)

            if not audio_file.is_absolute():
                audio_file = Path.cwd() / audio_file

            if not audio_file.exists():

                raise FileNotFoundError(
                    f"Audio file not found: {audio_file}"
                )

            transcription = self.client.transcribe(
                audio_path=str(audio_file),
                language=result["language"]
            )

            actual_text = transcription.get(
                "transcript",
                transcription.get("text", "")
            )

            if not actual_text.strip():

                raise ValueError(
                    "Indic-Transcribe returned an empty transcript."
                )

            speech_result = self.speech_checker.evaluate(
                audio_path=str(audio_file),
                expected_text=translation,
                actual_text=actual_text
            )

            speech_result["transcription"] = actual_text

            result["checks"]["speech"] = speech_result

            result["speech_status"] = speech_result.get(
                "status",
                "completed"
            )

        except Exception as exc:

            logger.exception(
                "Speech evaluation failed for %s",
                result["id"]
            )

            result["checks"]["speech"] = {
                "status": "error",
                "error": str(exc)
            }

            result["speech_status"] = "error"

            self.record_evaluation_error(
                result,
                "speech",
                exc
            )

    # =========================================================
    # SINGLE ITEM EVALUATION
    # =========================================================

    def evaluate_item(self, record):
        """Evaluate one dataset record."""

        item_id = record["id"]
        source = record["source"]
        language = record["lang"]

        translation = record.get("translation")
        audio_path = record.get("audio")

        result = {
            "id": item_id,
            "language": language,
            "source": source,
            "translation": translation,

            # Backward-compatible quality status.
            "status": "passed",

            # Explicit quality status.
            "translation_status": "passed",

            # Overall evaluation completeness.
            "evaluation_status": "completed",

            # Independent speech result.
            "speech_status": "not_run",

            "checks": {},

            # Actual translation-quality errors.
            "errors": [],

            # Infrastructure and evaluation errors.
            "evaluation_errors": []
        }

        # -----------------------------------------------------
        # 1. TRANSLATION AVAILABILITY
        # -----------------------------------------------------

        if not translation or not str(translation).strip():

            result["status"] = "failed"
            result["translation_status"] = "failed"

            result["errors"].append({
                "category": "missing_translation",
                "severity": "critical",
                "message": "No translation provided."
            })

            return result

        # -----------------------------------------------------
        # 2. DETERMINISTIC TRANSLATION CHECKS
        # -----------------------------------------------------

        try:

            deterministic = self.translation_checker.evaluate(
                item_id=item_id,
                source=source,
                translation=translation,
                language=language
            )

            result["checks"]["deterministic"] = deterministic

            self.update_status(
                result,
                deterministic.get("overall_status", "failed")
            )

            result["errors"].extend(
                deterministic.get("errors", [])
            )

        except Exception as exc:

            logger.exception(
                "Deterministic evaluation failed for %s",
                item_id
            )

            result["checks"]["deterministic"] = {
                "status": "error",
                "error": str(exc)
            }

            self.record_evaluation_error(
                result,
                "deterministic",
                exc
            )

        # -----------------------------------------------------
        # 3. BACK-TRANSLATION EVALUATION
        # -----------------------------------------------------

        if self.back_translation_checker:

            try:

                back_translation = (
                    self.back_translation_checker.evaluate(
                        source=source,
                        translation=translation,
                        source_language=language
                    )
                )

                result["checks"]["back_translation"] = (
                    back_translation
                )

                self.update_status(
                    result,
                    back_translation.get("status", "passed")
                )

            except Exception as exc:

                logger.exception(
                    "Back-translation failed for %s",
                    item_id
                )

                result["checks"]["back_translation"] = {
                    "status": "error",
                    "error": str(exc)
                }

                self.record_evaluation_error(
                    result,
                    "back_translation",
                    exc
                )

        # -----------------------------------------------------
        # 4. SPEECH EVALUATION
        # -----------------------------------------------------

        if self.speech_checker:

            self.evaluate_speech(
                result=result,
                translation=translation,
                audio_path=audio_path
            )

        # -----------------------------------------------------
        # 5. LLM JUDGING
        # -----------------------------------------------------

        if self.llm_judge:

            try:

                judge_result = self.llm_judge.evaluate(
                    source=source,
                    translation=translation,
                    source_language=record.get(
                        "source_lang", "auto"
                    ),
                    target_language=record.get(
                        "target_lang", language
                    )
                )

                result["checks"]["llm_judge"] = judge_result

                severity_status = {
                    "critical": "failed",
                    "major": "failed",
                    "minor": "warning",
                    "none": "passed"
                }

                judge_severity = judge_result.get(
                    "overall_severity",
                    "none"
                )

                self.update_status(
                    result,
                    severity_status.get(
                        judge_severity,
                        "warning"
                    )
                )

                # Add LLM-detected translation errors.
                for error in judge_result.get("errors", []):

                    result["errors"].append({
                        "category": error.get(
                            "category", "other"
                        ),
                        "severity": error.get(
                            "severity", "minor"
                        ),
                        "span": error.get("span", ""),
                        "message": error.get(
                            "explanation", ""
                        ),
                        "detected_by": "llm_judge"
                    })

            except Exception as exc:

                logger.exception(
                    "LLM judging failed for %s",
                    item_id
                )

                result["checks"]["llm_judge"] = {
                    "status": "error",
                    "error": str(exc)
                }

                # IMPORTANT:
                # An LLM API failure is not a translation error.
                self.record_evaluation_error(
                    result,
                    "llm_judge",
                    exc
                )

        return result

    # =========================================================
    # DATASET EVALUATION
    # =========================================================

    def evaluate_dataset(self, dataset_path):
        """Evaluate every dataset record independently."""

        records = self.load_dataset(dataset_path)

        results = []

        for record in records:

            logger.info(
                "Evaluating item: %s",
                record["id"]
            )

            result = self.evaluate_item(record)

            results.append(result)

        return results