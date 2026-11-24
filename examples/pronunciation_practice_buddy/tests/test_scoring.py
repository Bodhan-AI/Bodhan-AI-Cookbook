import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scoring import normalize, score_attempt  # noqa: E402


def test_normalize_drops_punctuation_and_danda():
    assert normalize("आप कैसे हैं?") == ["आप", "कैसे", "हैं"]
    assert normalize("मेरा नाम राहुल है।") == ["मेरा", "नाम", "राहुल", "है"]


def test_perfect_attempt():
    r = score_attempt("आप कैसे हैं?", "आप कैसे हैं")
    assert r.score == 100 and r.perfect


def test_missing_word_is_marked_missed():
    r = score_attempt("मुझे एक गिलास पानी चाहिए।", "मुझे गिलास पानी चाहिए")
    missed = [w.target for w in r.words if w.status == "missed"]
    assert missed == ["एक"]
    assert 0 < r.score < 100


def test_close_word_gets_partial_credit():
    r = score_attempt("स्टेशन कहाँ है", "स्टेशन कहां है")
    assert [w.status for w in r.words] == ["correct", "close", "correct"]
    assert r.score == round(100 * 2.5 / 3)


def test_extra_words_reported_but_not_penalised():
    r = score_attempt("यह कितने का है", "यह कितने का है ना")
    assert r.score == 100 and r.extra_words == ["ना"]


def test_empty_attempt_scores_zero():
    assert score_attempt("आप कैसे हैं", "").score == 0
