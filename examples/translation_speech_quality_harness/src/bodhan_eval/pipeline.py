import hashlib
from typing import Any

from .budget import Budget
from .checks import LangStats, registered_checks, run_text_checks
from .checks.reference import score_reference
from .combine import combine_flags
from .dataset import DatasetResult


def _translation_hash(translation: str | None) -> str | None:
    if translation is None:
        return None
    return hashlib.sha256(translation.encode("utf-8")).hexdigest()


def run_pipeline(result: DatasetResult, budget: Budget) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    length_ratios = [
        len(item.translation) / max(1, len(item.source))
        for item in result.items
        if item.translation
    ]
    if length_ratios:
        length_ratios.sort()
        median = length_ratios[len(length_ratios) // 2]
        mad = sorted(abs(value - median) for value in length_ratios)
        mad_value = mad[len(mad) // 2]
    else:
        median, mad_value = 1.0, 0.1
    stats = LangStats(median_length_ratio=median, mad_length_ratio=max(mad_value, 0.01))
    text_checks = registered_checks("text")

    for item in result.items:
        if not item.translation or not item.translation.strip():
            records.append({
                "id": item.id,
                "lang": item.lang,
                "translation_hash": _translation_hash(item.translation),
                "checks_run": [],
                "flags": [],
                "decisions": [{"check": "translation", "reason": "missing_translation"}],
                "terminal_state": "skipped",
                "skipped_reason": "missing_translation",
                "failed_checks": [],
                "reference_scores": None,
                "cost_inr": 0.0,
                "quarantined": False,
            })
            continue
        decisions = [
            {
                "check": spec.name,
                "reason": f"scheduled at cost tier {spec.cost_tier}",
            }
            for spec in text_checks
        ]
        try:
            flags = combine_flags([run_text_checks(item, stats)])
        except Exception as error:
            records.append({
                "id": item.id,
                "lang": item.lang,
                "translation_hash": _translation_hash(item.translation),
                "checks_run": [],
                "flags": [],
                "decisions": [{
                    "check": "registered_text_checks",
                    "reason": f"failed:{type(error).__name__}",
                }],
                "terminal_state": "failed_checks",
                "skipped_reason": None,
                "failed_checks": [f"text_checks:{type(error).__name__}"],
                "reference_scores": None,
                "cost_inr": 0.0,
                "quarantined": False,
            })
            continue
        records.append({
            "id": item.id,
            "lang": item.lang,
            "translation_hash": _translation_hash(item.translation),
            "checks_run": [spec.name for spec in text_checks],
            "flags": [
                {
                    "category": flag.category,
                    "severity": flag.severity,
                    "modality": flag.modality,
                    "span": flag.span,
                    "source_span": flag.source_span,
                    "audio_span_s": flag.audio_span_s,
                    "evidence": flag.evidence,
                    "detectors": flag.detectors,
                    "confidence": flag.confidence,
                    "suppressed_reason": flag.suppressed_reason,
                }
                for flag in flags
            ],
            "decisions": decisions,
            "terminal_state": "scored",
            "skipped_reason": None,
            "failed_checks": [],
            "reference_scores": score_reference(item.translation, item.reference),
            "cost_inr": 0.0,
            "quarantined": False,
        })
    for skipped in result.skipped:
        records.append({
            "id": skipped.id,
            "lang": skipped.lang,
            "checks_run": [],
            "flags": [],
            "decisions": [{
                "check": "language_filter",
                "reason": skipped.reason,
            }],
            "terminal_state": "skipped",
            "skipped_reason": skipped.reason,
            "failed_checks": [],
            "reference_scores": None,
            "cost_inr": 0.0,
            "quarantined": False,
        })
    for quarantined in result.quarantined:
        records.append({
            "id": f"quarantine:{quarantined.line}",
            "lang": quarantined.lang,
            "checks_run": [],
            "flags": [],
            "decisions": [{"check": "quarantine", "reason": quarantined.reason}],
            "terminal_state": "quarantined",
            "skipped_reason": quarantined.reason,
            "failed_checks": [],
            "reference_scores": None,
            "cost_inr": 0.0,
            "quarantined": True,
        })
    records.sort(key=lambda record: (record["id"], record["lang"]))
    return records
