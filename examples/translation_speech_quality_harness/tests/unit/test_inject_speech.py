"""Speech injector: waveform edits and their labels.

Audio is made of tone-words (``testing.stub_server.tone_word``): word k is a tone, and ``decode_tone_words``
reads back which words a clip contains. So each test knows exactly which words an edit cut, repeated or hid,
and checks that the label (placed through the proportional time map, as the waveform check places findings)
covers those words, within one word.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
import pytest

from bodhan_eval import audio as A
from bodhan_eval.checks.speech import AudioStatsCheck
from bodhan_eval.inject import speech
from bodhan_eval.inject.base import Edit, InjectContext
from bodhan_eval.inject.core import main
from bodhan_eval.models import Item
from bodhan_eval.normalize import word_tokens
from bodhan_eval.spans import overlaps
from bodhan_eval.testing.stub_server import SAMPLE_RATE as SR
from bodhan_eval.testing.stub_server import decode_tone_words, tone_word

DIGITS = "०१२३४५६७८९"
VOCAB = ["पद" + "".join(DIGITS[int(c)] for c in f"{k:03d}") for k in range(40)]  # 5 characters each
WORD_S, GAP_S, PAUSE_S, EDGE_S = 0.38, 0.1, 0.5, 0.3
N_WORDS, PER_SENTENCE = 24, 6
SEEDS = range(5)


def build(n: int = N_WORDS) -> tuple[np.ndarray, list[tuple[float, float]], str]:
    """Tone-words in sentences of PER_SENTENCE, with exact word times; the text has a danda per sentence."""
    parts, times, pos = [np.zeros(int(EDGE_S * SR), np.float32)], [], int(EDGE_S * SR)
    for k in range(n):
        if k:
            gap = np.zeros(int((PAUSE_S if k % PER_SENTENCE == 0 else GAP_S) * SR), np.float32)
            parts.append(gap)
            pos += len(gap)
        w = tone_word(k, WORD_S)
        parts.append(w)
        times.append((pos / SR, (pos + len(w)) / SR))
        pos += len(w)
    parts.append(np.zeros(int(EDGE_S * SR), np.float32))
    sentences = [" ".join(VOCAB[s : min(n, s + PER_SENTENCE)]) + "।" for s in range(0, n, PER_SENTENCE)]
    return np.concatenate(parts), times, " ".join(sentences)


AUDIO, TIMES, TEXT = build()


def as_saved(x: np.ndarray, tmp_path: Path) -> np.ndarray:
    """The clip as the injector reads it back from a 16-bit WAV."""
    A.write_wav(tmp_path / "ref.wav", x, SR)
    return A.load_audio(tmp_path / "ref.wav")[0]


@pytest.fixture
def item(tmp_path: Path) -> Item:
    path = tmp_path / "x.wav"
    A.write_wav(path, AUDIO, SR)
    return Item(id="x", lang="hi", source="Some English source text.", translation=TEXT, audio=str(path))


def apply(fn, item: Item, seed: int, tmp_path: Path) -> Edit:
    e = fn(item, random.Random(seed), InjectContext([item], audio_out=tmp_path))
    assert e is not None and e.audio is not None and e.sr == SR and e.translation is None
    return e


def label_words(e: Edit) -> set[int]:
    a, b = e.span
    return {
        k for k, t in enumerate(word_tokens(TEXT)) if (t.start < b and a < t.end) or (a == b and t.start <= a <= t.end)
    }


def words_between(t0: float, t1: float) -> set[int]:
    return {k for k, (a, b) in enumerate(TIMES) if a < t1 and t0 < b}


def near(label: set[int], truth: set[int]) -> bool:
    """Every truth word is labelled, give or take one word at each end, and the label strays at most one word."""
    if not truth:
        return False
    lo, hi = min(truth), max(truth)
    return bool(label) and min(label) >= lo - 1 and max(label) <= hi + 1 and len(truth - label) <= 2


def decoded(x: np.ndarray) -> list[int]:
    return decode_tone_words(x, SR)


def test_the_tone_words_decode() -> None:
    assert decoded(AUDIO) == list(range(N_WORDS))
    assert len(word_tokens(TEXT)) == N_WORDS


@pytest.mark.parametrize("seed", SEEDS)
def test_truncate_tail_cuts_the_last_words(item: Item, tmp_path: Path, seed: int) -> None:
    e = apply(speech.truncate_tail, item, seed, tmp_path)
    assert e.audio is not None
    kept = decoded(e.audio)
    assert kept == list(range(len(kept))) and 2 <= N_WORDS - len(kept) <= N_WORDS // 2
    assert np.array_equal(e.audio, as_saved(AUDIO, tmp_path)[: len(e.audio)])
    assert near(label_words(e), set(range(len(kept), N_WORDS)))


@pytest.mark.parametrize("seed", SEEDS)
def test_cut_middle_removes_a_stretch_of_words(item: Item, tmp_path: Path, seed: int) -> None:
    e = apply(speech.cut_middle, item, seed, tmp_path)
    assert e.audio is not None
    kept = decoded(e.audio)
    gone = set(range(N_WORDS)) - set(kept)
    assert gone and kept == sorted(kept) and max(gone) - min(gone) + 1 == len(gone)  # one contiguous stretch
    assert min(gone) > 0 and max(gone) < N_WORDS - 1  # in the middle
    assert near(label_words(e), gone)


@pytest.mark.parametrize("seed", SEEDS)
def test_repeat_stretch_says_some_words_twice(item: Item, tmp_path: Path, seed: int) -> None:
    e = apply(speech.repeat_stretch, item, seed, tmp_path)
    assert e.audio is not None and len(e.audio) > len(AUDIO)
    said = decoded(e.audio)
    twice = {w for w in said if said.count(w) == 2}
    assert twice and set(said) == set(range(N_WORDS)) and len(said) == N_WORDS + len(twice)
    assert near(label_words(e), twice)


@pytest.mark.parametrize("seed", SEEDS)
def test_insert_silence_adds_two_to_four_seconds_between_words(item: Item, tmp_path: Path, seed: int) -> None:
    e = apply(speech.insert_silence, item, seed, tmp_path)
    assert e.audio is not None
    added = (len(e.audio) - len(AUDIO)) / SR
    assert 2.0 <= added <= 4.0
    assert decoded(e.audio) == list(range(N_WORDS))
    # the silence goes into a quiet gap, so the first sample that differs is where the next word used to start
    t = np.flatnonzero(np.abs(e.audio[: len(AUDIO)] - as_saved(AUDIO, tmp_path)) > 1e-6)[0] / SR
    nxt = min(k for k, (a, _) in enumerate(TIMES) if a >= t - 0.05)
    assert label_words(e) and label_words(e) <= {nxt - 2, nxt - 1, nxt, nxt + 1}


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("fn", [speech.noise_burst, speech.clip_region], ids=lambda f: f.__name__)
def test_noise_and_clipping_hit_a_stretch_and_label_its_words(fn, item: Item, tmp_path: Path, seed: int) -> None:
    e = apply(fn, item, seed, tmp_path)
    assert e.audio is not None and len(e.audio) == len(AUDIO)
    changed = np.flatnonzero(np.abs(e.audio - as_saved(AUDIO, tmp_path)) > 1e-6)
    assert len(changed)
    truth = words_between(changed[0] / SR, changed[-1] / SR)
    assert near(label_words(e), truth)
    if fn is speech.clip_region:
        assert np.max(np.abs(e.audio)) >= 0.98


@pytest.mark.parametrize("fn", speech.ALL, ids=lambda f: f.__name__)
def test_speech_methods_need_audio(fn, tmp_path: Path) -> None:
    no_audio = Item(id="x", lang="hi", source="s", translation=TEXT)
    assert fn(no_audio, random.Random(0), InjectContext([no_audio], audio_out=tmp_path)) is None


@pytest.mark.parametrize("fn", speech.ALL, ids=lambda f: f.__name__)
def test_speech_methods_skip_a_clip_too_short_to_edit(fn, tmp_path: Path) -> None:
    x, _, text = build(2)
    path = tmp_path / "short.wav"
    A.write_wav(path, x, SR)
    short = Item(id="s", lang="hi", source="s", translation=text, audio=str(path))
    assert fn(short, random.Random(0), InjectContext([short], audio_out=tmp_path)) is None


@pytest.mark.parametrize("seed", range(3))
@pytest.mark.parametrize("fn", [speech.insert_silence, speech.clip_region], ids=lambda f: f.__name__)
def test_waveform_check_catches_silence_and_clipping_at_the_label(
    fn, seed: int, item: Item, tmp_path: Path, make_ctx, run_one, fuse_item
) -> None:
    """The loop in miniature for speech: tier-0 ``audio_stats`` flags the edit where the label says."""
    e = apply(fn, item, seed, tmp_path)
    assert e.audio is not None
    out = tmp_path / "edited.wav"
    A.write_wav(out, e.audio, SR)
    r = run_one(AudioStatsCheck, make_ctx(), item.source, TEXT, audio=str(out))
    flags, review = fuse_item(r)
    hits = [f for f in flags + review if f.category == "speech_garbled"]
    assert any(overlaps(f.span, e.span, tol=6) for f in hits), ([f.span for f in hits], e.span)


def test_cli_writes_edited_audio_next_to_the_labels(tmp_path: Path) -> None:
    A.write_wav(tmp_path / "x.wav", AUDIO, SR)
    row = {"id": "x", "lang": "hi", "source": "Some English source text.", "translation": TEXT, "audio": "x.wav"}
    (tmp_path / "in.jsonl").write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    args = ["--dataset", str(tmp_path / "in.jsonl"), "--out", str(tmp_path / "out.jsonl"), "--rate", "1"]
    speech_only = ["--categories", "speech_omission,speech_repetition,speech_garbled"]
    assert main([*args, *speech_only, "--audio-out", str(tmp_path / "audio")]) == 0
    out = json.loads((tmp_path / "out.jsonl").read_text(encoding="utf-8"))
    lab = out["labels"][0]
    assert lab["type"].startswith("speech_") and out["translation"] == TEXT
    assert out["audio"].startswith("audio/") and (tmp_path / out["audio"]).is_file()
    # without --audio-out there is nowhere to write edited audio: speech methods are not used
    assert main([*args, *speech_only, "--out", str(tmp_path / "o2.jsonl")]) == 0
    summary = json.loads((tmp_path / "o2.inject.json").read_text(encoding="utf-8"))
    assert summary["injected"] == 0 and summary["not_injectable"] == 1
