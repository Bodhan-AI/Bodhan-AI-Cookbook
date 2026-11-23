"""Speech check tables (TESTING.md §2): must-catch and must-not-flag cases on synthetic audio.

``audio_stats`` reads WAV files made with numpy. ``asr`` goes through the stub server, whose transcription
decodes tone-words (``testing.stub_server.tone_speech``): each vocabulary word is a tone, so a test decides
exactly what was spoken, and every chunking and second-opinion request is real.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from bodhan_eval import audio as A
from bodhan_eval.checks.speech import AsrCheck, AudioStatsCheck
from bodhan_eval.models import Item, ItemResult
from bodhan_eval.normalize import word_tokens
from bodhan_eval.pipeline import run_check
from bodhan_eval.testing.stub_server import SAMPLE_RATE as SR
from bodhan_eval.testing.stub_server import speech_like, tone_speech

DIGITS = "०१२३४५६७८९"
VOCAB = ["पद" + "".join(DIGITS[int(c)] for c in f"{k:03d}") for k in range(120)]  # 5 characters each
WORD_S = 0.38  # 5 characters at the 13 chars/s prior: duration checks see a normal speaking rate


def say(words: list[int], per_sentence: int = 8, pause_s: float = 0.5, word_s: float = WORD_S) -> np.ndarray:
    """Tone-words in sentences of ``per_sentence`` words with a pause between sentences."""
    parts = []
    for s in range(0, len(words), per_sentence):
        parts += [tone_speech(words[s : s + per_sentence], word_s=word_s), A.silence(pause_s, SR)]
    return np.concatenate(parts[:-1])


def text_of(words: list[int], vocab: list[str] = VOCAB) -> str:
    return " ".join(vocab[w] for w in words)


def words_in(text: str, span: tuple[int, int] | None) -> list[int]:
    """Indices of the text words a span touches."""
    if span is None:
        return []
    return [k for k, t in enumerate(word_tokens(text)) if t.start < span[1] and span[0] < t.end]


@pytest.fixture
def speech(start_stub, make_ctx, tmp_path: Path, fuse_item) -> Callable[..., tuple[ItemResult, list, list, list]]:
    """speech(text, audio, vocab=VOCAB, errors=[]) -> (result, flags, review queue, transcription requests)."""
    counter = [0]

    def run(text: str, audio: np.ndarray, vocab: list[str] = VOCAB, errors: list | None = None):
        counter[0] += 1
        path = tmp_path / f"a{counter[0]}.wav"
        A.write_wav(path, audio, SR)
        stub = start_stub({"transcribe": {"hi": {"tone_words": vocab, "errors": errors or []}}})
        ctx = make_ctx(stub)
        r = ItemResult(item=Item(id="t", lang="hi", source="s", translation=text, audio=str(path)))
        for check in (AudioStatsCheck(), AsrCheck()):
            r.checks[check.name] = run_check(check, r, ctx)
        flags, review = fuse_item(r)
        asr_requests = [q for q in stub.requests if q["path"].endswith("/transcriptions")]
        return r, flags, review, asr_requests

    return run


@pytest.fixture
def stats(make_ctx, tmp_path: Path, fuse_item) -> Callable[..., tuple[ItemResult, list, list]]:
    """stats(text, audio) -> (result, flags, review queue): the waveform check alone."""

    def run(text: str, audio: np.ndarray, name: str = "w.wav"):
        path = tmp_path / name
        if path.suffix == ".wav":
            A.write_wav(path, audio, SR)
        r = ItemResult(item=Item(id="t", lang="hi", source="s", translation=text, audio=str(path)))
        r.checks["audio_stats"] = run_check(AudioStatsCheck(), r, make_ctx())
        return (r, *fuse_item(r))

    return run


WORDS = list(range(24))
TEXT = text_of(WORDS)


def categories(flags: list) -> list[str]:
    return sorted(f.category for f in flags)


# ---------------------------------------------------------------------------
# audio_stats: must catch (each flags on its own; D25)
# ---------------------------------------------------------------------------


def with_inserted(x: np.ndarray, at_s: float, insert: np.ndarray) -> np.ndarray:
    k = int(at_s * SR)
    return np.concatenate([x[:k], insert, x[k:]])


def noise(seconds: float, level: float = 0.15) -> np.ndarray:
    return (np.random.default_rng(7).standard_normal(int(seconds * SR)) * level).astype(np.float32)


def clipped(x: np.ndarray, a_s: float, b_s: float) -> np.ndarray:
    y = x.copy()
    a, b = int(a_s * SR), int(b_s * SR)
    y[a:b] = np.clip(y[a:b] * 6.0, -1.0, 1.0)
    return y


def gap_after_word(words: list[int], k: int) -> float:
    """Time (s) of the gap right after word k in ``say(words)``."""
    return len(say(words[: k + 1])) / SR - 0.15 + 0.05  # end of word k (its sentence's trailing edge removed)


AUDIO_STATS_CATCH = [
    ("2 s silence inside the speech", lambda x: with_inserted(x, gap_after_word(WORDS, 11), A.silence(2.0, SR)), "Silence", {11, 12}),
    ("leading silence", lambda x: np.concatenate([A.silence(2.5, SR), x]), "leading silence", None),
    ("trailing silence", lambda x: np.concatenate([x, A.silence(2.5, SR)]), "trailing silence", None),
    ("clipping", lambda x: clipped(x, 6.0, 7.0), "Clipping", None),
    ("noise burst", lambda x: with_inserted(x, gap_after_word(WORDS, 15), noise(0.8)), "broadband noise", {15, 16}),
]  # fmt: skip


@pytest.mark.parametrize(
    ("make", "evidence", "near"), [c[1:] for c in AUDIO_STATS_CATCH], ids=[c[0] for c in AUDIO_STATS_CATCH]
)
def test_audio_stats_must_catch(stats, make, evidence: str, near: set[int] | None) -> None:
    r, flags, _ = stats(TEXT, make(say(WORDS)))
    hits = [f for f in flags if f.category == "speech_garbled" and any(evidence in e["detail"] for e in f.evidence)]
    assert hits, [e["detail"] for f in flags for e in f.evidence]
    if near is not None:  # located on the words around the event (proportional over speaking time)
        assert set(words_in(TEXT, hits[0].span)) & near


def test_audio_stats_edge_silence_is_placed_at_the_edge_of_the_text(stats) -> None:
    """Nothing is wrong with a word: the location is the insertion point before or after the text."""
    _, lead, _ = stats(TEXT, np.concatenate([A.silence(2.5, SR), say(WORDS)]))
    _, trail, _ = stats(TEXT, np.concatenate([say(WORDS), A.silence(2.5, SR)]))
    assert [f.span for f in lead] == [(0, 0)] and [f.span for f in trail] == [(len(TEXT), len(TEXT))]


@pytest.mark.parametrize(
    "audio", [np.zeros(0, dtype=np.float32), A.silence(3.0, SR)], ids=["no samples", "all silence"]
)
def test_audio_stats_empty_audio_is_garbled(stats, audio: np.ndarray) -> None:
    _, flags, _ = stats(TEXT, audio)
    assert categories(flags) == ["speech_garbled"] and flags[0].severity == "critical"


def test_audio_stats_short_audio_is_only_a_hint(stats) -> None:
    """Half the text's expected duration: a speech_omission hint for the review queue, never a flag on its own."""
    _, flags, review = stats(TEXT, say(WORDS[:10]))
    assert flags == [] and [f.category for f in review] == ["speech_omission"]


# ---------------------------------------------------------------------------
# audio_stats: must not flag
# ---------------------------------------------------------------------------

AUDIO_STATS_CLEAN = [
    ("tone speech with sentence pauses", lambda: say(WORDS)),
    ("natural short pauses (0.9 s)", lambda: say(WORDS, per_sentence=6, pause_s=0.6)),
    ("quiet but clean speech", lambda: say(WORDS) * 0.03),
    ("speech-like harmonic audio", lambda: speech_like(len(TEXT.replace(" ", "")) / 13.0)),
]


@pytest.mark.parametrize("make", [c[1] for c in AUDIO_STATS_CLEAN], ids=[c[0] for c in AUDIO_STATS_CLEAN])
def test_audio_stats_must_not_flag(stats, make) -> None:
    r, flags, review = stats(TEXT, make())
    assert flags == [] and review == [], [e["detail"] for f in flags + review for e in f.evidence]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed (CI installs it)")
def test_mp3_input_is_decoded_with_ffmpeg(stats, tmp_path: Path) -> None:
    wav, mp3 = tmp_path / "in.wav", tmp_path / "in.mp3"
    A.write_wav(wav, say(WORDS), SR)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), str(mp3)], check=True)
    r, flags, _ = stats(TEXT, np.zeros(0), name="in.mp3")
    assert r.checks["audio_stats"]["status"] == "ok" and flags == []
    assert abs(r.metrics["audio"]["duration_s"] - len(say(WORDS)) / SR) < 0.2


# ---------------------------------------------------------------------------
# asr: must catch
# ---------------------------------------------------------------------------

LONG = list(range(30))
LONG_TEXT = text_of(LONG)


def test_asr_catches_a_deletion_run_mid_text(speech) -> None:
    spoken = LONG[:12] + LONG[17:]
    r, flags, _, _ = speech(LONG_TEXT, say(spoken))
    om = [f for f in flags if f.category == "speech_omission"]
    assert om and set(words_in(LONG_TEXT, om[0].span)) == {12, 13, 14, 15, 16}
    assert "asr" in om[0].checks


def test_asr_catches_a_truncated_tail(speech) -> None:
    r, flags, _, _ = speech(LONG_TEXT, say(LONG[:24]))
    om = [f for f in flags if f.category == "speech_omission"]
    assert om and set(words_in(LONG_TEXT, om[0].span)) == set(range(24, 30))
    assert any("truncated" in e["detail"] for e in om[0].evidence)


def test_asr_catches_a_repeated_phrase(speech) -> None:
    spoken = LONG[:14] + LONG[10:14] + LONG[14:]
    _, flags, _, _ = speech(LONG_TEXT, say(spoken))
    rep = [f for f in flags if f.category == "speech_repetition"]
    assert rep and set(words_in(LONG_TEXT, rep[0].span)) <= set(range(9, 15))


def test_asr_catches_a_looped_trigram(speech) -> None:
    spoken = LONG[:23] + LONG[20:23] + LONG[20:23] + LONG[23:]
    _, flags, _, _ = speech(LONG_TEXT, say(spoken))
    rep = [f for f in flags if f.category == "speech_repetition"]
    assert rep and set(words_in(LONG_TEXT, rep[0].span)) & {20, 21, 22}


def read_out_vocab() -> list[str]:
    vocab = VOCAB.copy()
    vocab[100:104] = ["ट्वेंटी", "फाइव", "हंड्रेड", "थाउज़ेंड"]  # a number read out in English, as Indic-Speak does
    return vocab


NUM_TEXT = " ".join(["2,500" if k == 15 else VOCAB[k] for k in LONG])


def test_asr_catches_words_missing_next_to_a_number_read_as_words(speech) -> None:
    # seen in the demo: the words cut just before "15.2%" hid inside the number's read-out gap
    spoken = LONG[:10] + [100, 101, 102, 103] + LONG[16:]  # text words 10-14 not spoken, then the number
    _, flags, _, _ = speech(NUM_TEXT, say(spoken), vocab=read_out_vocab())
    om = [f for f in flags if f.category == "speech_omission"]
    assert om and set(words_in(NUM_TEXT, om[0].span)) == {10, 11, 12, 13, 14}


def test_asr_catches_a_phrase_repeated_inside_a_number_read_as_words(speech) -> None:
    # seen in the demo: "ঊনত্রিশ কোটি ঊনত্রিশ কোটি ..." where the text has digits only
    spoken = LONG[:15] + [100, 101, 100, 101, 102, 103] + LONG[16:]
    _, flags, _, _ = speech(NUM_TEXT, say(spoken), vocab=read_out_vocab())
    rep = [f for f in flags if f.category == "speech_repetition"]
    assert rep and set(words_in(NUM_TEXT, rep[0].span)) & {14, 15, 16}


def test_asr_number_read_as_words_alone_raises_nothing(speech) -> None:
    spoken = LONG[:15] + [100, 101, 102, 103] + LONG[16:]
    _, flags, review, _ = speech(NUM_TEXT, say(spoken), vocab=read_out_vocab())
    assert flags == [] and review == []


def test_asr_poor_transcript_everywhere_is_garbled(speech) -> None:
    junk = ["ञङञ" + "ङ" * (k % 3) for k in range(120)]  # nothing like the text
    r, flags, _, _ = speech(LONG_TEXT, say(LONG), vocab=junk)
    assert "speech_garbled" in categories(flags)
    assert r.metrics["asr_residual_cer"] >= 0.55


# ---------------------------------------------------------------------------
# asr: must not flag
# ---------------------------------------------------------------------------


def test_asr_clean_speech_raises_nothing(speech) -> None:
    r, flags, review, _ = speech(LONG_TEXT, say(LONG))
    assert flags == [] and review == [] and r.metrics["asr_cer"] == 0.0


def test_asr_scattered_substitutions_are_not_omissions(speech) -> None:
    spoken = list(LONG)
    for k, other in ((4, 90), (13, 91), (22, 92)):  # three isolated words heard as other words
        spoken[k] = other
    _, flags, review, _ = speech(LONG_TEXT, say(spoken))
    assert flags == [] and review == []


def test_asr_numbers_spoken_as_words(speech) -> None:
    vocab = VOCAB.copy()
    vocab[100:105] = ["पच्चीस", "दो", "हज़ार", "पाँच", "सौ"]
    text = " ".join(["25" if k == 5 else "2,500" if k == 18 else VOCAB[k] for k in LONG])
    spoken = [100 if k == 5 else k for k in LONG if k != 18]
    spoken[spoken.index(19) : spoken.index(19)] = [101, 102, 103, 104]  # "2,500" -> four spoken words
    _, flags, _, _ = speech(text, say(spoken), vocab=vocab)
    assert flags == []


def test_asr_short_item_with_a_number_read_as_words_is_not_garbled(speech) -> None:
    """Seen live (23 Nov, Tamil): '365' was read out in English as five words. In a five-word sentence that
    alone made the character error rate 106 %, and the item was flagged as garbled speech."""
    vocab = VOCAB.copy()
    vocab[100:105] = ["த்ரீ", "ஹண்ட்ரட்", "அண்ட்", "சிக்ஸ்டி", "ஃபைவ்"]
    text = " ".join([VOCAB[0], VOCAB[1], "365", VOCAB[3], VOCAB[4]])
    r, flags, review, _ = speech(text, say([0, 1, 100, 101, 102, 103, 104, 3, 4]), vocab=vocab)
    assert flags == [] and review == []
    assert r.metrics["asr_residual_cer"] == 0.0 and r.metrics["asr_numbers_read_out"] == 1


def test_asr_garbled_speech_around_a_number_is_still_caught(speech) -> None:
    junk = ["ञङञ" + "ङ" * (k % 3) for k in range(120)]
    text = " ".join([*(VOCAB[k] for k in range(12)), "365", *(VOCAB[k] for k in range(13, 24))])
    _, flags, _, _ = speech(text, say(list(range(24))), vocab=junk)
    assert "speech_garbled" in categories(flags)


def test_spoken_length_counts_digits_as_read_out_words() -> None:
    from bodhan_eval.checks.speech import spoken_chars

    assert spoken_chars("abc 365") == 3 + 3 * 5  # ~5 characters of speech per digit
    assert spoken_chars("abc def") == 6


def test_asr_nukta_and_candrabindu_variants_match(speech) -> None:
    vocab = VOCAB.copy()
    vocab[3], vocab[9] = "जमीन", "चांद"  # the ASR writes them without nukta / with anusvara
    text = " ".join(["ज़मीन" if k == 3 else "चाँद" if k == 9 else VOCAB[k] for k in LONG])
    r, flags, review, _ = speech(text, say(LONG), vocab=vocab)
    assert flags == [] and review == [] and r.metrics["asr_cer"] == 0.0


def test_asr_truncation_is_not_also_garbled(speech) -> None:
    _, flags, _, _ = speech(LONG_TEXT, say(LONG[:16]))  # nearly half the text never spoken
    assert "speech_omission" in categories(flags) and "speech_garbled" not in categories(flags)


def test_asr_long_audio_is_sent_in_chunks_under_30_s(speech) -> None:
    words = list(range(120))
    text = text_of(words)
    r, flags, _, requests = speech(text, say(words))  # ~70 s
    assert len(requests) >= 6 and all(q["seconds"] <= 29.0 for q in requests)
    assert flags == [] and r.metrics["asr_cer"] == 0.0 and r.metrics["asr_chunks"] == len(requests)


# ---------------------------------------------------------------------------
# second opinion: an ASR mistake that depends on where the audio was cut is dismissed
# ---------------------------------------------------------------------------


def test_second_opinion_dismisses_words_lost_at_chunk_ends(speech) -> None:
    """The ASR drops the last 3 words of every 6-14.5 s request: the ~10 s chunks lose words, the long
    second-opinion chunk does not, so the 'omissions' are dismissed as checker noise."""
    words = list(range(44))
    text = text_of(words)
    r, flags, review, requests = speech(text, say(words), errors=[{"min_s": 6, "max_s": 14.5, "drop_last_words": 3}])
    assert r.metrics["asr_chunks"] >= 2
    assert [f for f in flags + review if f.category == "speech_omission" and "asr" in f.checks] == []
    assert r.dismissed and all("second transcription" in d["reason"] for d in r.dismissed)
    assert len(requests) == r.metrics["asr_chunks"] + 1  # one long second-opinion chunk


def test_second_opinion_dismisses_an_asr_loop(speech) -> None:
    words = list(range(44))
    text = text_of(words)
    errors = [{"min_s": 6, "max_s": 14.5, "repeat_last_words": 3}]
    r, flags, review, _ = speech(text, say(words), errors=errors)
    assert [f for f in flags if f.category == "speech_repetition"] == []
    assert any("second transcription" in d["reason"] for d in r.dismissed)


def test_a_real_omission_survives_the_second_opinion(speech) -> None:
    words = list(range(44))
    text = text_of(words)
    spoken = words[:30] + words[35:]
    r, flags, _, _ = speech(text, say(spoken))
    om = [f for f in flags if f.category == "speech_omission"]
    assert om and set(words_in(text, om[0].span)) == set(range(30, 35))
    assert any("confirmed by a second transcription" in e["detail"] for e in om[0].evidence)
