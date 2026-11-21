import pytest

from bodhan_eval.labels import canonical_category, canonical_severity


@pytest.mark.parametrize(
    ("raw", "modality", "expected"),
    [
        ("number", "text", "number"),
        ("UNIT", "text", "number"),
        ("term_entity", "text", "term"),
        ("wrong-script", "text", "language"),
        ("markdown", "text", "format"),
        ("truncation", "speech", "speech_omission"),
        ("omission_speech", "speech", "speech_omission"),
        ("garbled", "speech", "garbled"),
    ],
)
def test_canonical_category(raw, modality, expected):
    assert canonical_category(raw, modality) == expected


def test_unknown_category_is_unmapped():
    assert canonical_category("made-up-category", "text") == "unmapped"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("minor", "minor"),
        ("low", "minor"),
        ("major", "major"),
        ("medium", "major"),
        ("critical", "critical"),
        ("high", "critical"),
        ("unknown", "major"),
    ],
)
def test_canonical_severity(raw, expected):
    assert canonical_severity(raw) == expected
