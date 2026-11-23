
from bodhan_eval.audio_checks import evaluate_audio_quality


def good_metrics():
    return {
        "duration_seconds": 2.5,
        "sample_rate": 24000,
        "channels": 1,
        "silence_ratio": 0.30,
        "clipping_ratio": 0.0,
    }


def test_good_audio_passes_all_checks():
    checks = evaluate_audio_quality(good_metrics())
    assert all(check["passed"] is True for check in checks)


def test_very_short_audio_fails():
    metrics = good_metrics()
    metrics["duration_seconds"] = 0.1

    checks = evaluate_audio_quality(metrics)
    duration_check = next(
        c for c in checks if c["category"] == "audio_duration"
    )

    assert duration_check["passed"] is False


def test_high_silence_requires_review():
    metrics = good_metrics()
    metrics["silence_ratio"] = 0.90

    checks = evaluate_audio_quality(metrics)
    silence_check = next(
        c for c in checks if c["category"] == "audio_silence"
    )

    assert silence_check["status"] == "review"
    assert silence_check["passed"] is None


def test_clipping_requires_review():
    metrics = good_metrics()
    metrics["clipping_ratio"] = 0.02

    checks = evaluate_audio_quality(metrics)
    clipping_check = next(
        c for c in checks if c["category"] == "audio_clipping"
    )

    assert clipping_check["status"] == "review"


def test_low_sample_rate_requires_review():
    metrics = good_metrics()
    metrics["sample_rate"] = 8000

    checks = evaluate_audio_quality(metrics)
    rate_check = next(
        c for c in checks if c["category"] == "audio_sample_rate"
    )

    assert rate_check["status"] == "review"
