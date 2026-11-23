"""Span maths and alignment helpers (TESTING.md §1)."""

from __future__ import annotations

import itertools

import pytest

from bodhan_eval.normalize import ws_tokens
from bodhan_eval.spans import (
    char_error_rate,
    narrowest,
    overlaps,
    similarity,
    span_to_token_range,
    tokens_to_span,
    union,
    word_alignment,
)


@pytest.mark.parametrize(
    ("a", "b", "tol", "expected"),
    [
        pytest.param((0, 5), (4, 8), 2, True, id="partial-overlap"),
        pytest.param((0, 5), (5, 8), 2, False, id="touching-half-open"),
        pytest.param((2, 3), (0, 10), 2, True, id="contained"),
        pytest.param((5, 5), (6, 8), 2, True, id="zero-width-within-tolerance"),
        pytest.param((5, 5), (8, 9), 2, False, id="zero-width-beyond-tolerance"),
        pytest.param((5, 5), (4, 6), 0, True, id="zero-width-inside-no-tolerance"),
        pytest.param((5, 5), (5, 5), 2, True, id="both-zero-width-same-point"),
        pytest.param(None, (0, 5), 2, False, id="none-left"),
        pytest.param((0, 5), None, 2, False, id="none-right"),
    ],
)
def test_overlaps(a, b, tol: int, expected: bool) -> None:
    assert overlaps(a, b, tol) is expected
    assert overlaps(b, a, tol) is expected  # symmetric


def test_union_and_narrowest() -> None:
    assert union(None, (1, 2)) == (1, 2)
    assert union((5, 9), (1, 6)) == (1, 9)
    assert narrowest([(0, 10), (2, 4), (5, 5), None]) == (2, 4)  # zero-width only if nothing else
    assert narrowest([(7, 7), (3, 3)]) == (3, 3)
    assert narrowest([None]) is None


TEXT = "पानी समुद्र तल पर 100 °C पर उबलता है।"


def test_tokens_to_span_and_back_are_inverse() -> None:
    toks = ws_tokens(TEXT)
    for i, j in itertools.combinations_with_replacement(range(len(toks)), 2):
        span = tokens_to_span(toks, i, j)
        assert span is not None
        assert TEXT[span[0] : span[1]].startswith(toks[i].text)
        assert span_to_token_range(toks, span) == (i, j)


@pytest.mark.parametrize(
    ("i", "j", "expected"),
    [
        pytest.param(-1, 2, None, id="negative-start"),
        pytest.param(3, 2, None, id="reversed"),
        pytest.param(99, 99, None, id="start-out-of-range"),
        pytest.param(8, 99, "end-clipped", id="end-clipped"),
    ],
)
def test_tokens_to_span_rejects_bad_indices(i: int, j: int, expected) -> None:
    toks = ws_tokens(TEXT)
    span = tokens_to_span(toks, i, j)
    if expected is None:
        assert span is None
    else:
        assert span == (toks[8].start, toks[-1].end)


def test_tokens_to_span_on_empty_tokens() -> None:
    assert tokens_to_span([], 0, 0) is None


def test_span_to_token_range_outside_text() -> None:
    assert span_to_token_range(ws_tokens(TEXT), (500, 600)) is None


@pytest.mark.parametrize(
    ("ref", "hyp", "expected"),
    [
        pytest.param(
            "a b c d", "a c d", [("equal", 0, 1, 0, 1), ("delete", 1, 2, 1, 1), ("equal", 2, 4, 1, 3)], id="deletion"
        ),
        pytest.param(
            "a b c", "a b x c", [("equal", 0, 2, 0, 2), ("insert", 2, 2, 2, 3), ("equal", 2, 3, 3, 4)], id="insertion"
        ),
        pytest.param(
            "a b c", "a z c", [("equal", 0, 1, 0, 1), ("replace", 1, 2, 1, 2), ("equal", 2, 3, 2, 3)], id="replacement"
        ),
        pytest.param("a b", "a b", [("equal", 0, 2, 0, 2)], id="identical"),
    ],
)
def test_word_alignment_opcodes(ref: str, hyp: str, expected: list) -> None:
    assert word_alignment(ref.split(), hyp.split()) == expected


@pytest.mark.parametrize(
    ("ref", "hyp", "expected"),
    [
        pytest.param("", "", 0.0, id="both-empty"),
        pytest.param("", "abc", 1.0, id="empty-ref"),
        pytest.param("abc", "", 1.0, id="empty-hyp"),
        pytest.param("abc", "abd", 1 / 3, id="one-substitution"),
        pytest.param("पानी", "पानी", 0.0, id="identical-indic"),
    ],
)
def test_char_error_rate(ref: str, hyp: str, expected: float) -> None:
    assert char_error_rate(ref, hyp) == pytest.approx(expected)


def test_similarity_bounds() -> None:
    assert similarity("", "") == 1.0
    assert similarity("abc", "abc") == 1.0
    assert similarity("abc", "xyz") == 0.0
