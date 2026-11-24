import json
from pathlib import Path

from sklearn.metrics import (
    precision_score,
    recall_score,
    f1_score,
    confusion_matrix,
    classification_report
)


class CalibrationEngine:
    """
    Evaluates IndicEval predictions against labelled ground truth.

    Supports:
    - Single-label category classification.
    - Binary error detection.
    - Multi-label category evaluation.
    - Threshold tuning.
    - JSON calibration reports.
    """

    CATEGORIES = [
        "number_error",
        "omission",
        "repetition",
        "formatting_error",
        "addition",
        "meaning_error",
        "fluency",
        "grammar",
        "terminology",
        "other",
        "none"
    ]

    CATEGORY_ALIASES = {
        "number": "number_error",
        "number_mismatch": "number_error",
        "formatting": "formatting_error",
        "format": "formatting_error",
        "punctuation": "formatting_error",
        "meaning": "meaning_error",
        "no_error": "none",
        "no_errors": "none",
        "": "none"
    }

    def __init__(self):
        self.results = {}

    @classmethod
    def normalize_label(cls, label):
        """Normalize a category label."""

        if label is None:
            return "none"

        normalized = str(label).strip().lower()

        if normalized in cls.CATEGORY_ALIASES:
            return cls.CATEGORY_ALIASES[normalized]

        if normalized not in cls.CATEGORIES:
            return "other"

        return normalized

    @classmethod
    def normalize_predictions(cls, prediction):
        """
        Accept either one category or a list of categories.
        """

        if prediction is None:
            return ["none"]

        if isinstance(prediction, str):
            prediction = [prediction]

        labels = {
            cls.normalize_label(label)
            for label in prediction
        }

        if not labels or labels == {"none"}:
            return ["none"]

        labels.discard("none")

        return sorted(labels)

    def evaluate(self, ground_truth, predictions):
        """
        Single-label classification metrics.

        ground_truth: list of actual error categories.
        predictions: list of predicted error categories.
        """

        if len(ground_truth) != len(predictions):
            raise ValueError(
                "Ground-truth and prediction lengths must match."
            )

        if not ground_truth:
            raise ValueError("Calibration dataset is empty.")

        actual = [
            self.normalize_label(label)
            for label in ground_truth
        ]

        predicted = [
            self.normalize_label(label)
            for label in predictions
        ]

        labels = sorted(set(actual) | set(predicted))

        precision = precision_score(
            actual,
            predicted,
            labels=labels,
            average=None,
            zero_division=0
        )

        recall = recall_score(
            actual,
            predicted,
            labels=labels,
            average=None,
            zero_division=0
        )

        f1 = f1_score(
            actual,
            predicted,
            labels=labels,
            average=None,
            zero_division=0
        )

        matrix = confusion_matrix(
            actual,
            predicted,
            labels=labels
        )

        per_category = {}

        for index, label in enumerate(labels):
            per_category[label] = {
                "precision": round(float(precision[index]), 4),
                "recall": round(float(recall[index]), 4),
                "f1_score": round(float(f1[index]), 4),
                "support": int(actual.count(label))
            }

        report = classification_report(
            actual,
            predicted,
            labels=labels,
            output_dict=True,
            zero_division=0
        )

        accuracy = sum(
            actual_label == predicted_label
            for actual_label, predicted_label
            in zip(actual, predicted)
        ) / len(actual)

        self.results = {
            "total_examples": len(actual),
            "labels": labels,
            "overall": {
                "accuracy": round(float(accuracy), 4),
                "macro_precision": round(
                    float(precision_score(
                        actual,
                        predicted,
                        average="macro",
                        zero_division=0
                    )),
                    4
                ),
                "macro_recall": round(
                    float(recall_score(
                        actual,
                        predicted,
                        average="macro",
                        zero_division=0
                    )),
                    4
                ),
                "macro_f1": round(
                    float(f1_score(
                        actual,
                        predicted,
                        average="macro",
                        zero_division=0
                    )),
                    4
                ),
                "weighted_f1": round(
                    float(f1_score(
                        actual,
                        predicted,
                        average="weighted",
                        zero_division=0
                    )),
                    4
                )
            },
            "per_category": per_category,
            "confusion_matrix": matrix.tolist(),
            "classification_report": report
        }

        return self.results

    def evaluate_detection(self, ground_truth, predictions):
        """
        Evaluate whether an error was detected, regardless of category.

        ground_truth: list of actual error categories.
        predictions: list of predicted categories or category lists.
        """

        if len(ground_truth) != len(predictions):
            raise ValueError(
                "Ground-truth and prediction lengths must match."
            )

        if not ground_truth:
            raise ValueError("Detection dataset is empty.")

        # Normalize ground truth exactly like predictions so both
        # single-label and multi-label inputs are handled consistently.
        actual = []

        for label in ground_truth:
            categories = self.normalize_predictions(label)

            actual.append(
                0 if categories == ["none"] else 1
            )

        predicted = []

        for prediction in predictions:
            categories = self.normalize_predictions(prediction)

            predicted.append(
                0 if categories == ["none"] else 1
            )

        return {
            "total_examples": len(actual),
            "actual_error_count": int(sum(actual)),
            "predicted_error_count": int(sum(predicted)),
            "precision": round(
                float(precision_score(
                    actual,
                    predicted,
                    zero_division=0
                )),
                4
            ),
            "recall": round(
                float(recall_score(
                    actual,
                    predicted,
                    zero_division=0
                )),
                4
            ),
            "f1_score": round(
                float(f1_score(
                    actual,
                    predicted,
                    zero_division=0
                )),
                4
            ),
            "confusion_matrix": confusion_matrix(
                actual,
                predicted,
                labels=[0, 1]
            ).tolist()
        }

    def evaluate_multilabel(self, ground_truth, predictions):
        """
        Multi-label category evaluation.

        Each example may have multiple actual or predicted categories.
        """

        if len(ground_truth) != len(predictions):
            raise ValueError(
                "Ground-truth and prediction lengths must match."
            )

        if not ground_truth:
            raise ValueError("Calibration dataset is empty.")

        actual_sets = [
            set(self.normalize_predictions(labels))
            for labels in ground_truth
        ]

        predicted_sets = [
            set(self.normalize_predictions(labels))
            for labels in predictions
        ]

        labels = sorted(
            set().union(*actual_sets, *predicted_sets)
        )

        actual_matrix = [
            [int(label in row) for label in labels]
            for row in actual_sets
        ]

        predicted_matrix = [
            [int(label in row) for label in labels]
            for row in predicted_sets
        ]

        return {
            "total_examples": len(ground_truth),
            "labels": labels,
            "micro_precision": round(
                float(precision_score(
                    actual_matrix,
                    predicted_matrix,
                    average="micro",
                    zero_division=0
                )),
                4
            ),
            "micro_recall": round(
                float(recall_score(
                    actual_matrix,
                    predicted_matrix,
                    average="micro",
                    zero_division=0
                )),
                4
            ),
            "micro_f1": round(
                float(f1_score(
                    actual_matrix,
                    predicted_matrix,
                    average="micro",
                    zero_division=0
                )),
                4
            ),
            "macro_precision": round(
                float(precision_score(
                    actual_matrix,
                    predicted_matrix,
                    average="macro",
                    zero_division=0
                )),
                4
            ),
            "macro_recall": round(
                float(recall_score(
                    actual_matrix,
                    predicted_matrix,
                    average="macro",
                    zero_division=0
                )),
                4
            ),
            "macro_f1": round(
                float(f1_score(
                    actual_matrix,
                    predicted_matrix,
                    average="macro",
                    zero_division=0
                )),
                4
            )
        }

    @staticmethod
    def tune_threshold(
        scores,
        ground_truth,
        thresholds=None
    ):
        """
        Find the threshold with the highest F1 score.

        scores: confidence scores between 0 and 1.
        ground_truth: binary labels (1 = error, 0 = no error).
        """

        if len(scores) != len(ground_truth):
            raise ValueError(
                "Scores and ground-truth lengths must match."
            )

        if not scores:
            raise ValueError("Threshold dataset is empty.")

        if any(not 0 <= score <= 1 for score in scores):
            raise ValueError(
                "All confidence scores must be between 0 and 1."
            )

        if any(label not in (0, 1) for label in ground_truth):
            raise ValueError(
                "Ground-truth labels must be binary (0 or 1)."
            )

        if thresholds is None:
            thresholds = [
                round(x / 100, 2)
                for x in range(30, 91, 5)
            ]

        if not thresholds:
            raise ValueError("Threshold list is empty.")

        best_result = None

        for threshold in thresholds:

            predictions = [
                1 if score >= threshold else 0
                for score in scores
            ]

            current_f1 = f1_score(
                ground_truth,
                predictions,
                zero_division=0
            )

            if (
                best_result is None
                or current_f1 > best_result["f1_score"]
            ):
                best_result = {
                    "threshold": threshold,
                    "f1_score": round(
                        float(current_f1), 4
                    ),
                    "precision": round(
                        float(precision_score(
                            ground_truth,
                            predictions,
                            zero_division=0
                        )),
                        4
                    ),
                    "recall": round(
                        float(recall_score(
                            ground_truth,
                            predictions,
                            zero_division=0
                        )),
                        4
                    )
                }

        return best_result

    def save(self, output_path):
        """Save the most recently computed results as JSON."""

        if not self.results:
            raise ValueError(
                "Run evaluate() before saving results."
            )

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with path.open("w", encoding="utf-8") as file:
            json.dump(
                self.results,
                file,
                indent=4,
                ensure_ascii=False
            )

        return str(path)