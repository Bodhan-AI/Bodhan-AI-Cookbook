"""Speech checks. Text/transcript pairs are real day-1 Indic-Speak inputs and Indic-Transcribe outputs; audio is synthetic."""

import json
import shutil
import subprocess

import numpy as np
import pytest
from conftest import DATA, make_clients, make_wav

from bodhan_eval import checks_speech as cs
from bodhan_eval.checks_text import robust_z
from bodhan_eval.clients import NotRun

SPEECH = json.loads((DATA / "speech.json").read_text(encoding="utf-8"))
SR = 24000
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def speechlike(seconds=4.0, amp=0.4):
    """Syllable-like bursts: 150 ms of tone, 60 ms of quiet."""
    t = np.arange(int(seconds * SR)) / SR
    env = ((t % 0.21) < 0.15).astype(np.float32)
    return (amp * env * np.sin(2 * np.pi * 180 * t)).astype(np.float32)


def kinds(cands):
    return [(c["category"], c["class"]) for c in cands]


# ---------------------------------------------------------------- audio statistics

def test_clean_speechlike_audio_raises_nothing():
    x = speechlike()
    assert cs.stats_candidates(cs.audio_stats(x, SR)) == []


def test_silent_audio_is_garbled():
    assert kinds(cs.stats_candidates(cs.audio_stats(np.zeros(SR * 3, np.float32), SR))) == [("garbled", "exact")]


def test_clipped_audio_is_garbled():
    x = np.clip(speechlike() * 6, -1, 1)
    assert ("garbled", "exact") in kinds(cs.stats_candidates(cs.audio_stats(x, SR)))


def test_long_internal_silence_is_located():
    x = np.concatenate([speechlike(2), np.zeros(int(2.5 * SR), np.float32), speechlike(2)])
    c = cs.stats_candidates(cs.audio_stats(x, SR))
    assert kinds(c) == [("garbled", "exact")]
    a, b = c[0]["audio_span_s"]
    assert 1.9 <= a <= 2.1 and 4.4 <= b <= 4.6


def test_noise_has_no_quiet_gaps():
    x = (np.random.default_rng(0).standard_normal(SR * 3) * 0.2).astype(np.float32)
    assert kinds(cs.stats_candidates(cs.audio_stats(x, SR))) == [("garbled", "noisy")]


# ---------------------------------------------------------------- audio input

def test_wav_passes_through_and_empty_is_not_run():
    wav = make_wav(1.0)
    assert cs.to_wav(wav) == wav
    with pytest.raises(NotRun):
        cs.to_wav(b"")


@needs_ffmpeg
def test_mp3_and_stereo_are_converted(tmp_path):
    src = tmp_path / "a.wav"
    src.write_bytes(make_wav(1.0, rate=16000))
    mp3 = tmp_path / "a.mp3"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(src), "-ac", "2", str(mp3)], check=True)
    x, sr = cs.read_samples(cs.to_wav(mp3.read_bytes()))
    assert sr == 24000 and 0.9 < len(x) / sr < 1.2
    with pytest.raises(NotRun, match="not readable"):
        cs.to_wav(b"this is not audio at all, just text" * 20)


def test_long_audio_is_chunked_under_the_window(tmp_path, fake_bodhan, fake_llm):
    x = np.concatenate([speechlike(70)])
    parts = cs.chunks(x, SR)
    assert len(parts) == 3 and all((b - a) / SR <= cs.CHUNK_MAX_S for a, b in parts)
    assert parts[0][0] == 0 and parts[-1][1] == len(x)
    bodhan, _ = make_clients(tmp_path, fake_bodhan, fake_llm)
    text, info = cs.transcribe_long(x, SR, "hi", bodhan)
    assert text == "transcript transcript transcript" and len(fake_bodhan.requests) == 3 and len(info) == 3


# ---------------------------------------------------------------- alignment on real transcripts

@pytest.mark.parametrize("name", ["hi_S1", "ta_S1", "bn_S1", "hi_B5_markdown", "hi_P1"])
def test_real_clean_transcripts_raise_nothing(name):
    # Includes numbers spoken as English words in native script, an invented word, and LaTeX read aloud.
    s = SPEECH[name]
    al = cs.align(s["text"], s["transcript"])
    assert al["findings"] == [] and cs.align_candidates(al, {"voiced_s": s["seconds"]}) == []


def test_real_looped_audio_is_repetition():
    s = SPEECH["hi_P1_looped_40s"]
    al = cs.align(s["text"], s["transcript"])
    assert [f["shape"] for f in al["findings"]] == ["repetition"]


def test_real_noise_transcript_is_garbled():
    s = SPEECH["noise_3s"]
    c = cs.align_candidates(cs.align(s["text"], s["transcript"]), {"voiced_s": 3.0})
    assert ("garbled", "noisy") in kinds(c)


def test_cut_ending_is_truncation_with_text_span():
    s = SPEECH["hi_P1"]
    words = s["transcript"].split()
    al = cs.align(s["text"], " ".join(words[:-5]))
    assert [f["shape"] for f in al["findings"]] == ["truncation"]
    a, b = al["findings"][0]["span"]
    assert b == len(s["text"].rstrip("।")) or s["text"][a:b] in s["text"]


def test_missing_middle_words_is_omission():
    s = SPEECH["hi_P1"]
    words = s["transcript"].split()
    al = cs.align(s["text"], " ".join(words[:8] + words[13:]))
    assert [f["shape"] for f in al["findings"]] == ["omission"]


def test_doubled_phrase_is_repetition():
    s = SPEECH["hi_P1"]
    words = s["transcript"].split()
    al = cs.align(s["text"], " ".join(words[:6] + words[3:6] + words[6:]))
    assert [f["shape"] for f in al["findings"]] == ["repetition"]


def test_single_word_slips_are_ignored():
    s = SPEECH["hi_P1"]
    words = s["transcript"].split()
    words[4] = "x"
    del words[10]
    assert cs.align(s["text"], " ".join(words))["findings"] == []


def test_speaking_rate_outliers():
    rows = [(i, 0.09 + 0.002 * (i % 3), True) for i in range(9)] + [(9, 0.03, True)]
    out = cs.speaking_rate_candidates(rows, robust_z, 8, 3.0)
    assert list(out) == [9] and out[9][0]["category"] == "speech_omission"
    assert cs.speaking_rate_candidates(rows[:5], robust_z, 8, 3.0) == {}


def test_long_missing_tail_is_exact_short_one_noisy():
    s = SPEECH["hi_P1"]
    words = s["transcript"].split()
    short = cs.align_candidates(cs.align(s["text"], " ".join(words[:-5])), {"voiced_s": 0})
    long = cs.align_candidates(cs.align(s["text"], " ".join(words[: len(words) // 2])), {"voiced_s": 0})
    assert kinds(short) == [("speech_omission", "noisy")] and kinds(long) == [("speech_omission", "exact")]


def test_number_read_twice_is_not_a_loop():
    al = cs.align("मिशन 2023 में शुरू हुआ और 2023 में उतरा।", "मिशन ट्वेंटी ट्वेंटी थ्री में शुरू हुआ और ट्वेंटी ट्वेंटी थ्री में उतरा")
    assert al["findings"] == []


def test_recheck_clears_omission_heard_in_the_cut():
    s = SPEECH["hi_P1"]
    words = s["transcript"].split()
    al = cs.align(s["text"], " ".join(words[:8] + words[13:]))
    f = al["findings"][0]
    x = np.zeros(24000 * 10, dtype=np.float32)
    calls = []

    class Fake:
        def __init__(self, reply):
            self.reply = reply

        def transcribe(self, wav, lang, ctx=None):
            calls.append(len(wav))
            return self.reply

    assert cs.recheck_omission(x, 24000, s["text"], f, "hi", Fake(" ".join(words[6:15]))) >= cs.RECHECK_HEARD
    assert cs.recheck_omission(x, 24000, s["text"], f, "hi", Fake("कुछ और")) == 0
    assert calls[0] < len(cs.wav_bytes(x, 24000))  # only a cut was sent


def test_recheck_confirms_a_repeat_heard_again_in_the_cut():
    text = "इस कदम से भूमि को बदलने में भी मदद मिलेगी।"
    al = cs.align(text, "इस कदम से भूमि को बदलने में भी मदद को बदलने में भी मदद मिलेगी")
    f = next(f for f in al["findings"] if f["shape"] == "repetition")
    x = np.zeros(24000 * 8, dtype=np.float32)

    class Fake:
        def __init__(self, reply):
            self.reply = reply

        def transcribe(self, wav, lang, ctx=None):
            return self.reply

    assert cs.recheck_repetition(x, 24000, text, f, "hi", Fake("भूमि को बदलने में भी मदद को बदलने में भी मदद मिलेगी"))
    assert not cs.recheck_repetition(x, 24000, text, f, "hi", Fake("भूमि को बदलने में भी मदद मिलेगी"))  # heard once: transcriber loop
