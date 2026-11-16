"""Turn candidates into flags.

shortcut: interim rule (exact -> flag, noisy -> review, weak -> dropped) until Stage 6 adds grouping, corroboration and calibration.
"""

SPAN_SIDE = "source"  # which side `span` reports; both sides are always written

DEFAULT_SEVERITY = {"number": "major", "wrong_language": "major", "omission": "major", "addition": "major", "format": "major",
                    "mistranslation": "major", "term": "major", "speech_truncation": "major", "speech_omission": "major",
                    "speech_repetition": "major", "garbled": "major"}


def to_flag(c: dict) -> dict:
    primary = c["source_span"] if SPAN_SIDE == "source" else c["translation_span"]
    span_in = SPAN_SIDE if primary else ("translation" if SPAN_SIDE == "source" else "source")
    return {
        "category": c["category"], "severity": c["severity_hint"] or DEFAULT_SEVERITY.get(c["category"], "major"),
        "confidence": c["strength"], "span": primary or c["translation_span"] or c["source_span"], "span_in": span_in,
        "source_span": c["source_span"], "translation_span": c["translation_span"], "audio_span_s": c.get("audio_span_s"),
        "checks": [c["check"]], "evidence": [{"check": c["check"], **c["evidence"]}],
    }


def combine(candidates: list[dict]) -> tuple[str, list[dict], list[dict]]:
    serious = {"major", "critical"}
    flags = [to_flag(c) for c in candidates if c["class"] == "exact" or (c["class"] == "judge" and c["severity_hint"] in serious)]
    review = [to_flag(c) for c in candidates if c["class"] == "noisy" or (c["class"] == "judge" and c["severity_hint"] not in serious)]
    status = "flagged" if flags else ("review" if review else "clean")
    return status, flags, review
