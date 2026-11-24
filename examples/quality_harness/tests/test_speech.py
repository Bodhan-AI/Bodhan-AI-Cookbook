import numpy as np

from bodhan_eval import audio as A
from bodhan_eval.speech import asr_candidates, check_speech, rate_candidate, signal_candidates

RATE = 16000
TEXT = "राम ने 25 आम खाए। सीता ने मीठे केले खाए। फिर वे दोनों घर गए।"
HEARD = "राम ने ट्वेंटी फाइव आम खाए सीता ने मीठे केले खाए फिर वे दोनों घर गए"


def speechlike(seconds=6.0, gaps=(1.5, 3.0, 4.5)):
    """Tones with short pauses, like clean TTS: 0.2 s gaps, peak 0.3."""
    t = np.arange(int(seconds * RATE)) / RATE
    x = 0.3 * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    for g in gaps:
        x[int(g * RATE):int((g + 0.2) * RATE)] = 0
    return A.Audio(x.astype(np.float32), RATE)


def kinds(cands):
    return sorted((c.category, c.evidence["kind"]) for c in cands)


def heard(text, seconds=6.0):
    return asr_candidates(TEXT, [(0.0, seconds, text)], speechlike(seconds))


def test_a_faithful_transcript_with_numbers_as_words_raises_nothing():
    assert heard(HEARD) == []
    assert heard(HEARD.replace("मीठे", "मिठे")) == []  # a near-miss spelling is transcriber noise


def test_missing_words_repeated_words_and_truncation_are_found_and_located():
    (c,) = heard(HEARD.replace("सीता ने मीठे केले खाए ", ""))
    assert (c.category, c.evidence["kind"], c.evidence["missing_words"]) == ("speech_omission", "omission", 5)
    assert TEXT[slice(*c.span)] == "सीता ने मीठे केले खाए" and 0 < c.evidence["time"][0] < c.evidence["time"][1] < 6
    (c,) = heard(HEARD.replace("दोनों घर गए", "दोनों घर गए घर गए"))
    assert (c.category, c.evidence["kind"]) == ("speech_repetition", "repeated_words")
    (c,) = heard("राम ने ट्वेंटी फाइव आम खाए सीता ने")
    assert c.evidence["kind"] == "truncation" and c.severity == "critical" and c.standalone
    assert kinds(heard("")) == [("speech_garbled", "nothing_recognised")]


def test_clean_audio_passes_and_signal_faults_are_found():
    clean = speechlike()
    assert signal_candidates(clean, TEXT) == []
    gap = A.concat(A.cut(clean, 0, 2.5), A.Audio(np.zeros(int(2.5 * RATE), np.float32), RATE), A.cut(clean, 2.5, 6))
    (c,) = signal_candidates(gap, TEXT)
    assert c.evidence["kind"] == "silence" and c.standalone and 2.4 <= c.evidence["time"][0] <= 2.6
    noisy = speechlike()
    noisy.samples[RATE * 2:RATE * 3] += np.random.default_rng(0).normal(0, 0.3, RATE).astype(np.float32)
    assert kinds(signal_candidates(noisy, TEXT)) == [("speech_garbled", "noise")]
    loud = A.Audio(np.clip(clean.samples * 4, -1, 1), RATE)
    assert kinds(signal_candidates(loud, TEXT)) == [("speech_garbled", "clipping")]
    assert kinds(signal_candidates(A.Audio(np.zeros(RATE, np.float32), RATE), TEXT)) == [("speech_omission", "silent_audio")]


def test_rate_check_and_a_full_run_through_files(tmp_path):
    assert rate_candidate(TEXT, len(TEXT) / 10, 10.0) is None
    assert rate_candidate(TEXT, len(TEXT) / 10 / 3, 10.0).evidence["kind"] == "short_audio"

    class Fake:
        def __init__(self):
            self.calls = []

        def available(self, model):
            return True

        def transcribe(self, path, lang):
            self.calls.append((path.read_bytes()[:4], lang))
            return HEARD

    path = A.save(speechlike(), tmp_path / "a.wav")
    fake = Fake()
    cands, info, statuses = check_speech(TEXT, path, "hi", fake, tmp_path / "pieces")
    assert cands == [] and statuses == {"speech_signal": "ok", "speech_asr": "ok"} and fake.calls == [(b"RIFF", "hi")]
    assert info["transcript"] == HEARD and info["duration_s"] == 6.0
    (bad,), _, _ = check_speech(TEXT, tmp_path / "missing.wav", "hi", fake, tmp_path / "pieces")
    assert bad.evidence["kind"] == "unreadable" and bad.severity == "critical"


def test_injector_edits_are_labelled_and_the_signal_faults_are_caught(tmp_path):
    from bodhan_eval.inject_speech import inject

    clean = speechlike(8.0, gaps=(1.5, 3.0, 4.5, 6.0))
    path = A.save(clean, tmp_path / "base.hi.wav")
    base = {"id": "fort", "lang": "hi", "source": "The fort...", "translation": TEXT,
            "speech": {"audio": str(path), "duration_s": clean.duration}}
    rows = inject([base], tmp_path, tmp_path / "inj", per_category=1, negatives=4, seed=0)
    by_op = {r["injection"]["op"]: r for r in rows if "base" in r}
    assert rows[0]["audio"] == "base.hi.wav" and set(by_op) == {
        "truncation", "omission", "repetition", "silence", "noise", "clipping", "quieter", "louder", "end_silence", "faint_hiss"}
    dur = {op: A.load(tmp_path / r["audio"]).duration for op, r in by_op.items()}
    assert dur["truncation"] < 0.85 * 8 and dur["omission"] < 8 < dur["repetition"] and dur["silence"] >= 9.5
    lab = by_op["truncation"]["labels"][0]
    assert lab["type"] == "speech_omission" and lab["span"][1] == len(TEXT) and lab["time"][1] == 8.0
    for op in ("silence", "noise", "clipping"):
        found = signal_candidates(A.load(tmp_path / by_op[op]["audio"]), TEXT)
        assert {c.evidence["kind"] for c in found} == {op}, op
    for op in ("quieter", "louder", "end_silence", "faint_hiss"):
        assert by_op[op]["labels"] == [] and signal_candidates(A.load(tmp_path / by_op[op]["audio"]), TEXT) == [], op


def test_transcriber_failures_are_retried_and_contradicted_omissions_downgraded(tmp_path):
    from bodhan_eval.speech import consistency

    class Flaky:
        def __init__(self):
            self.n = 0

        def available(self, model):
            return True

        def transcribe(self, path, lang):
            self.n += 1
            return "राम ୄୄୄୄୄୄୄୄୄୄ" if self.n == 1 else HEARD  # degenerate first, fine on the shorter retry

    clean = speechlike(10.0, gaps=(2.0, 4.0, 6.0, 8.0))
    cands, info, _ = check_speech(TEXT, A.save(clean, tmp_path / "a.wav"), "hi", Flaky(), tmp_path / "p")
    assert info["retried_pieces"] == 1 and len(info["pieces"]) >= 2
    miss = [{"check": "speech_asr", "category": "speech_omission", "standalone": True, "evidence": {"share": 0.45}}]
    consistency(miss, ratio=1.0)
    assert not miss[0]["standalone"] and miss[0]["evidence"]["likely"].startswith("transcriber miss")
    real = [{"check": "speech_asr", "category": "speech_omission", "standalone": True, "evidence": {"share": 0.45}}]
    consistency(real, ratio=0.6)  # the audio is short too: consistent, so it stands
    assert real[0]["standalone"]


def test_second_listen_tells_a_transcriber_miss_from_words_never_spoken(tmp_path):
    gap = HEARD.replace("सीता ने मीठे केले खाए ", "")

    class Ears:
        def __init__(self, again):
            self.again, self.n = again, 0

        def available(self, model):
            return True

        def transcribe(self, path, lang):
            self.n += 1
            return gap if self.n == 1 else self.again  # whole clip first, then the window around the gap

    path = A.save(speechlike(), tmp_path / "a.wav")
    (miss,), _, _ = check_speech(TEXT, path, "hi", Ears("खाए सीता ने मीठे केले खाए फिर"), tmp_path / "p")
    assert not miss.standalone and miss.evidence["second_listen"]["heard_share"] == 1.0
    assert miss.evidence["likely"].startswith("transcriber miss")
    (real,), _, _ = check_speech(TEXT, path, "hi", Ears("आम खाए फिर वे"), tmp_path / "p")
    assert real.standalone and real.evidence["second_listen"]["heard_share"] < 0.5  # "खाए" also occurs nearby
