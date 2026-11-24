"""K5: where in the text a moment of audio falls, and how audio is cut into ASR chunks.

Audio here is tone-words (see ``testing.stub_server.tone_speech``): word k of the vocabulary is a tone, so
tests control exactly which word is spoken when, at what speed, and where the pauses are.
"""

from __future__ import annotations

import numpy as np

from bodhan_eval import audio as A
from bodhan_eval.checks.speech import AsrCheck, AudioStatsCheck, anchored_map, proportional_map
from bodhan_eval.models import Item, ItemResult
from bodhan_eval.normalize import normalize_text, word_tokens
from bodhan_eval.pipeline import run_check
from bodhan_eval.testing.stub_server import SAMPLE_RATE as SR
from bodhan_eval.testing.stub_server import tone_speech, tone_word

DIGITS = "०१२३४५६७८९"
VOCAB = ["पद" + "".join(DIGITS[int(c)] for c in f"{k:03d}") for k in range(120)]  # पद००० ... all the same length


def text_of(words: list[int]) -> str:
    return " ".join(VOCAB[w] for w in words)


def word_at(text: str, char: int) -> int:
    """Index of the word that contains (or starts right after) a character offset."""
    toks = word_tokens(text)
    return next((k for k, t in enumerate(toks) if char < t.end), len(toks) - 1)


def seconds(x: np.ndarray) -> float:
    return len(x) / SR


# ---------------------------------------------------------------------------
# chunk plans: ~10 s at pauses for anchors; 29 s where needed; a different plan for the second opinion
# ---------------------------------------------------------------------------


def sentences(n: int, words: int = 8, pause_s: float = 0.5) -> np.ndarray:
    """n sentences of ``words`` tone-words (~3.2 s each), separated by ``pause_s`` of silence."""
    return np.concatenate(
        [
            np.concatenate([tone_speech([(s * words + k) % 100 for k in range(words)]), A.silence(pause_s, SR)])
            for s in range(n)
        ]
    )


def cuts_s(plan: list[tuple[int, int]]) -> list[float]:
    return [b / SR for _, b in plan[:-1]]


def is_quiet(x: np.ndarray, sample: int) -> bool:
    db = A.frame_db(x, SR)
    thr = max(-60.0, float(np.percentile(db, 90)) - 30.0)
    return bool(db[min(len(db) - 1, sample // int(SR * A.FRAME_S))] < thr)


def test_short_audio_is_one_chunk() -> None:
    x = sentences(3)  # ~11 s
    assert A.chunk_plan(x, SR) == [(0, len(x))]


def test_long_audio_is_cut_at_pauses_near_every_10_s() -> None:
    x = sentences(14)  # ~52 s
    plan = A.chunk_plan(x, SR)
    lengths = [(b - a) / SR for a, b in plan]
    assert plan[0][0] == 0 and plan[-1][1] == len(x)
    assert all(a == b for (_, a), (b, _) in zip(plan, plan[1:]))  # contiguous
    assert all(6.0 <= s <= 14.0 for s in lengths[:-1]) and lengths[-1] <= 14.0
    assert all(is_quiet(x, b) for _, b in plan[:-1])  # every cut falls in a pause


def test_without_nearby_pauses_a_chunk_stretches_up_to_29_s() -> None:
    x = sentences(4, words=56, pause_s=0.6)  # ~22 s sentences with only short word gaps inside
    plan = A.chunk_plan(x, SR)
    lengths = [(b - a) / SR for a, b in plan]
    assert len(plan) > 1 and all(s <= 29.0 for s in lengths)
    assert any(s > 14.0 for s in lengths)  # no sentence pause near 10 s: it cut later, at a real pause


def test_unbroken_sound_is_cut_by_the_29_s_rule() -> None:
    x = np.concatenate([tone_word(5, 0.3) for _ in range(250)])  # 75 s, no pause at all
    lengths = [(b - a) / SR for a, b in A.chunk_plan(x, SR)]
    assert all(s <= 29.0 for s in lengths) and all(s >= 18.0 for s in lengths[:-1])


def test_unbroken_sound_that_fits_in_one_request_is_not_cut() -> None:
    # 14-18 s left with no pause used to search for a cut 18 s ahead, past the end (ValueError on argmin)
    for total_s in (15.0, 16.5, 17.7, 33.0, 35.1, 46.5):
        x = np.concatenate([tone_word(5, 0.3) for _ in range(round(total_s / 0.3))])
        plan = A.chunk_plan(x, SR)
        assert plan[0][0] == 0 and plan[-1][1] == len(x)
        assert all(a == b for (_, a), (b, _) in zip(plan, plan[1:]))
        assert all(0 < (b - a) / SR <= 29.0 for a, b in plan)
        if total_s <= 29.0:
            assert plan == [(0, len(x))]


def test_every_request_stays_under_30_s() -> None:
    for x in (sentences(25), sentences(5, words=70)):
        assert all((b - a) / SR <= 29.0 for a, b in A.chunk_plan(x, SR) + A.alt_chunk_plan(x, SR, A.chunk_plan(x, SR)))


def test_second_opinion_on_long_audio_uses_long_chunks_away_from_the_primary_cuts() -> None:
    x = sentences(20)  # ~74 s
    primary = A.chunk_plan(x, SR)
    alt = A.alt_chunk_plan(x, SR, primary)
    assert all((b - a) / SR <= 29.0 for a, b in alt) and all((b - a) / SR >= 14.0 for a, b in alt[:-1])
    assert all(min(abs(c - p) for p in cuts_s(primary)) >= 1.5 for c in cuts_s(alt))


def test_second_opinion_on_mid_length_audio_is_one_chunk() -> None:
    x = sentences(5)  # ~18 s: two primary chunks
    assert len(A.chunk_plan(x, SR)) == 2
    assert A.alt_chunk_plan(x, SR, A.chunk_plan(x, SR)) == [(0, len(x))]


def test_second_opinion_on_short_audio_splits_near_the_middle() -> None:
    x = sentences(2)  # ~7 s: one primary chunk
    alt = A.alt_chunk_plan(x, SR, A.chunk_plan(x, SR))
    assert len(alt) == 2 and 0.3 < alt[0][1] / len(x) < 0.7 and is_quiet(x, alt[0][1])


# ---------------------------------------------------------------------------
# time -> text position
# ---------------------------------------------------------------------------


def test_proportional_map_skips_pauses() -> None:
    """A 3 s pause must not push later positions forward (the old wall-clock mapping put word 10 at word 13)."""
    words = list(range(20))
    text = text_of(words)
    first, second = tone_speech(words[:10]), tone_speech(words[10:])
    x = np.concatenate([first, A.silence(3.0, SR), second])
    tm = proportional_map(x, SR, text)
    t_mid_word_10 = seconds(first) + 3.0 + 0.15 + 0.125
    assert word_at(text, tm.char_at(t_mid_word_10)) == 10
    assert tm.method == "proportional"


def anchored(words_fast: int, words_slow: int) -> tuple[str, np.ndarray, object, float]:
    words = list(range(words_fast + words_slow))
    text = text_of(words)
    fast, slow = tone_speech(words[:words_fast], word_s=0.2), tone_speech(words[words_fast:], word_s=0.6)
    x = np.concatenate([fast, slow])
    toks = word_tokens(text)
    ref = [normalize_text(t.text, asr_fold=True) for t in toks]
    hyp_chunk = [0] * words_fast + [1] * words_slow
    tm = anchored_map(x, SR, text, toks, ref, ref, hyp_chunk, [(0, len(fast)), (len(fast), len(x))])
    return text, x, tm, seconds(fast)


def test_anchors_follow_a_change_in_speaking_rate() -> None:
    text, _, tm, t_slow = anchored(15, 15)
    t_mid_word_20 = t_slow + 0.15 + 5 * 0.7 + 0.3  # five slow words (0.6 s + 0.1 s gap) into the slow part
    assert word_at(text, tm.char_at(t_mid_word_20)) == 20
    assert tm.method == "asr_anchors"
    _, x, _, _ = anchored(15, 15)
    whole = proportional_map(x, SR, text)
    assert word_at(text, whole.char_at(t_mid_word_20)) != 20  # one proportion for the whole clip gets it wrong


def test_span_covers_at_least_one_whole_word() -> None:
    text, _, tm, t_slow = anchored(10, 10)
    a, b = tm.span(t_slow + 0.2, t_slow + 0.25)
    assert b > a and text[a:b].strip() == text[a:b] and " " not in text[a:b]


def test_times_outside_the_audio_clamp_to_the_ends() -> None:
    text, x, tm, _ = anchored(5, 5)
    assert tm.char_at(-1.0) == 0 and tm.char_at(seconds(x) + 5) == len(text)


# ---------------------------------------------------------------------------
# end to end: the transcript's anchors re-locate a waveform finding
# ---------------------------------------------------------------------------


def test_asr_anchors_relocate_a_silence_onto_the_right_words(start_stub, make_ctx, tmp_path) -> None:
    words = list(range(30))
    text = text_of(words)
    x = np.concatenate(
        [
            tone_speech(words[:15], word_s=0.2),  # fast
            tone_speech(words[15:25], word_s=0.6),  # slow
            A.silence(1.5, SR),  # the finding: a long silence between words 24 and 25
            tone_speech(words[25:], word_s=0.6),
        ]
    )
    path = tmp_path / "a.wav"
    A.write_wav(path, x, SR)
    stub = start_stub({"transcribe": {"hi": {"tone_words": VOCAB}}})
    ctx = make_ctx(stub)
    r = ItemResult(item=Item(id="t", lang="hi", source="s", translation=text, audio=str(path)))
    for check in (AudioStatsCheck(), AsrCheck()):
        r.checks[check.name] = run_check(check, r, ctx)
    assert r.checks["asr"]["status"] == "ok"
    silence = [s for s in r.signals if s.check == "audio_stats" and "Silence" in s.evidence]
    assert len(silence) == 1
    s = silence[0]
    assert s.details["located_by"] == "asr_anchors"
    assert {word_at(text, s.span[0]), word_at(text, s.span[1] - 1)} <= {24, 25}
    assert all((q["seconds"] or 0) <= 29.0 for q in stub.requests if q["path"].endswith("transcriptions"))
