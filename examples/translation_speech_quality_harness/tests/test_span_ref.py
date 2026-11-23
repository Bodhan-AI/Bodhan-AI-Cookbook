"""Span matching must work whether labels point into the translation or into the source."""

from bodhan_eval.calibrate import _matches
from bodhan_eval.models import Label


def _label(span):
    return Label(type="number", severity="major", span=tuple(span), annotator="self")


def _prediction(span=None, source_span=None):
    return {"category": "number", "span": span, "source_span": source_span}


def test_auto_matches_a_label_given_in_source_coordinates():
    prediction = _prediction(span=(40, 46), source_span=(14, 20))
    matched, overlap = _matches(_label((14, 20)), prediction, span_ref="auto")
    assert matched and overlap == 1.0


def test_auto_matches_a_label_given_in_translation_coordinates():
    prediction = _prediction(span=(40, 46), source_span=(14, 20))
    matched, overlap = _matches(_label((40, 46)), prediction, span_ref="auto")
    assert matched and overlap == 1.0


def test_auto_rejects_a_label_overlapping_neither_span():
    prediction = _prediction(span=(40, 46), source_span=(14, 20))
    matched, _ = _matches(_label((100, 110)), prediction, span_ref="auto")
    assert not matched


def test_translation_mode_ignores_source_span():
    prediction = _prediction(span=(40, 46), source_span=(14, 20))
    assert _matches(_label((14, 20)), prediction, span_ref="translation")[0] is False
    assert _matches(_label((40, 46)), prediction, span_ref="translation")[0] is True


def test_source_mode_ignores_translation_span():
    prediction = _prediction(span=(40, 46), source_span=(14, 20))
    assert _matches(_label((40, 46)), prediction, span_ref="source")[0] is False
    assert _matches(_label((14, 20)), prediction, span_ref="source")[0] is True


def test_prediction_without_the_selected_span_falls_back_to_category_match():
    prediction = _prediction(span=None, source_span=(14, 20))
    matched, overlap = _matches(_label((40, 46)), prediction, span_ref="translation")
    assert matched and overlap is None


def test_wrong_category_never_matches():
    prediction = {"category": "term", "span": (14, 20), "source_span": (14, 20)}
    assert _matches(_label((14, 20)), prediction, span_ref="auto")[0] is False
