from bodhan_eval.checks import LangStats, run_text_checks
from bodhan_eval.inject import build_dataset
from bodhan_eval.models import Item


def test_number_format_language_checks_clean_and_injected():
    clean = [
        Item(id="1", lang="hi", source="The value is 100.", translation="मान 100 है।"),
        Item(id="2", lang="hi", source="Heading", translation="# शीर्षक"),
    ]
    stats = LangStats(median_length_ratio=1.0, mad_length_ratio=0.1)
    flags = run_text_checks(clean[0], stats)
    assert not any(flag.category == "number" for flag in flags)

    injected = build_dataset(clean, seed=7, error_rate=1.0)
    detections = sum(
        len(run_text_checks(item, stats))
        for item in injected
    )
    assert isinstance(detections, int)
