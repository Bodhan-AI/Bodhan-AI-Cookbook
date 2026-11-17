"""Speech repetition, adaptive silence, and transcript reconciliation on synthetic inputs."""

import math
from array import array

from bodhan_eval.audio_checks import (
    SAMPLE_RATE,
    Audio,
    corroborate_speech,
    reconcile_signal_with_transcript,
    signal_checks,
    speech_findings,
)
from bodhan_eval.calibration import accepted, fit_thresholds
from bodhan_eval.schema import Finding, Span

THRESHOLDS = fit_thresholds([])


def speech_like(seconds: float, peak: float) -> array:
    """Syllable-rate modulated harmonics with short low-level pauses, scaled to a given peak."""
    state, values = 12345, []
    for index in range(round(seconds * SAMPLE_RATE)):
        t = index / SAMPLE_RATE
        state = (1103515245 * state + 12345) % 2**31
        noise = (state / 2**31 - 0.5) * 0.004
        # A 150 ms pause every second, like the gap between words or phrases.
        envelope = 0.0 if t % 1.0 > 0.85 else 0.25 + 0.75 * abs(math.sin(2 * math.pi * 3 * t))
        carrier = 0.6 * math.sin(2 * math.pi * 150 * t) + 0.4 * math.sin(2 * math.pi * 900 * t)
        values.append(round(peak * (envelope * carrier + noise)))
    return array("h", values)


def zeros(seconds: float) -> array:
    """Digital silence."""
    return array("h", [0]) * round(seconds * SAMPLE_RATE)


def silence_findings(samples: array) -> list[Finding]:
    """Return only the silence findings for a signal."""
    return [finding for finding in signal_checks(Audio(samples))[1] if finding.check == "silence"]


def repetitions(expected: str, transcript: str) -> list[Finding]:
    """Return repetition findings that pass the default thresholds."""
    _, findings = speech_findings(expected, transcript, 9000)
    return [finding for finding in accepted(findings, THRESHOLDS) if finding.category == "speech_repetition"]


HINDI = "मैंने 24 किताबें पढ़ीं और फिर स्कूल गया, जहाँ शिक्षक ने हमें गणित पढ़ाया।"


def test_whole_utterance_repetition_with_asr_variation():
    """A full repeat is found even when ASR spells a number as words and drops a nasal mark."""
    transcript = "मैंने चौबीस किताबें पढ़ीं और फिर स्कूल गया जहां शिक्षक ने हमें गणित पढ़ाया मैंने 24 किताबें पढ़ी और फिर स्कूल गया जहाँ शिक्षक ने हमें गणित पढ़ाया"
    metrics, findings = speech_findings(HINDI, transcript, 9000)
    assert metrics["cer"] > 1 and metrics["repeat_adjusted_cer"] < 0.1
    [repeat] = [finding for finding in accepted(findings, THRESHOLDS) if finding.category == "speech_repetition"]
    # The audio interval is estimated from the transcript position. The text stays the anchor.
    assert repeat.span.field == "audio_ms" and repeat.anchor.field == "transcript"
    assert transcript[repeat.anchor.start : repeat.anchor.end] == repeat.evidence
    assert "शिक्षक ने हमें गणित" in repeat.evidence
    # The insertions are explained by the repeat, so they are not reported as garbled speech.
    assert not any(finding.category == "garbled_audio" for finding in findings)


def test_noisy_english_whole_repeat_and_clause_repeat():
    """Rotated or slightly misrecognised copies and a single repeated clause are both strong."""
    script = "Mix 24 grams of salt into the warm water, then stir it for two minutes before serving."
    noisy = (
        "mix twenty four grams of salt in the warm water then stir it for two minutes before serving "
        "mix 24 gram of salt into a warm water then stir for two minute before serving"
    )
    assert repetitions(script, noisy)
    clause = "Open the window slowly, then close the door behind you and sit down."
    [repeat] = repetitions(clause, "open the window slowly then close the door behind you then close the door behind you and sit down")
    assert repeat.evidence == "then close the door behind you"


def test_looped_phrase_and_restart():
    """A phrase spoken three times and a restarted opening are both repeats."""
    script = "Please wash your hands carefully before you eat dinner tonight."
    looped = "please wash your hands carefully wash your hands carefully wash your hands carefully before you eat dinner tonight"
    [repeat] = repetitions(script, looped)
    assert repeat.evidence == "wash your hands carefully wash your hands carefully"
    restart = "The quick brown fox jumps over the lazy dog near the river bank."
    assert repetitions(restart, "the quick brown fox jumps the quick brown fox jumps over the lazy dog near the river bank")


def test_repetition_false_alarm_guards():
    """Scripted repeats, clean noisy ASR, moved clauses and a two-word seam are not repeats."""
    scripted = "Say thank you very much, and again say thank you very much."
    assert repetitions(scripted, "say thank you very much and again say thank you very much") == []
    assert repetitions(HINDI, "मैंने चौबीस किताबें पढ़ीं और फिर स्कूल गया जहां शिक्षक ने हमें गणित पढ़ाया") == []
    moved = "First boil the rice. Then chop the onions finely."
    _, findings = speech_findings(moved, "then chop the onions finely first boil the rice", 5000)
    assert not any(finding.category == "speech_repetition" for finding in findings)
    clause = "Open the window slowly, then close the door behind you and sit down."
    assert repetitions(clause, "open the window slowly then close the door the door behind you and sit down") == []
    # A one-word list item spoken twice stays below the threshold for corroboration to decide.
    items = "Bring Thiruvananthapuram, Bengaluru, and Chennai."
    _, findings = speech_findings(items, "bring thiruvananthapuram thiruvananthapuram bengaluru and chennai", 4000)
    assert all(finding.strength < 0.72 for finding in findings if finding.category == "speech_repetition")


def test_corroboration_matches_repeats_by_script_not_string():
    """A second segmentation may cut the repeated copy differently and still confirm it."""
    script = "Open the window slowly, then close the door behind you and sit down."
    first = "open the window slowly then close the door behind you then close the door behind you and sit down"
    second = "open the window slowly then close the door behind you and then close the door behind you and sit down"
    _, findings = speech_findings(script, first, 6000, script_field="source")
    updated, confirmed, _ = corroborate_speech(script, second, 6000, findings, script_field="source")
    assert confirmed == ["speech_repetition"]
    assert any(finding.category == "speech_repetition" and finding.strength >= 0.87 for finding in updated)


def test_quiet_continuous_speech_is_not_silence():
    """A voice peaking near -35 dBFS is judged against its own level, and the decision is scale-invariant."""
    quiet = speech_like(6.0, 500)
    metrics, findings = signal_checks(Audio(quiet))
    assert not [finding for finding in findings if finding.check == "silence"]
    assert metrics["silence_ratio"] < 0.3
    assert silence_findings(speech_like(6.0, 10000)) == []


def test_internal_digital_gap_is_flagged_in_quiet_speech():
    """Three seconds of digital silence inside a quiet recording is still a strong silence finding."""
    # 1.8 s ends before the signal's own pause, so the gap boundaries are exact.
    samples = speech_like(1.8, 500) + zeros(3.0) + speech_like(2.0, 500)
    [finding] = accepted(silence_findings(samples), THRESHOLDS)
    assert finding.span == Span("audio_ms", 1800, 4800)
    assert finding.strength == 0.99
    assert "Internal" in finding.evidence and "3.00 s" in finding.evidence


def test_normal_edge_padding_is_ignored_but_long_trailing_silence_is_not():
    """Sub-second TTS padding is normal. Silence longer than the speech it follows is not."""
    padded = zeros(0.6) + speech_like(4.0, 6000) + zeros(0.9)
    assert silence_findings(padded) == []
    trailing = speech_like(2.5, 6000) + zeros(4.0)
    [finding] = silence_findings(trailing)
    assert finding.span == Span("audio_ms", 2500, 6500)
    assert finding.evidence.startswith("Trailing")
    assert accepted([finding], THRESHOLDS)


def test_effectively_silent_clips_get_one_finding():
    """All-zero and below-floor clips are flagged once over the whole clip."""
    for samples in (zeros(4.0), array("h", [(-1) ** index * 20 for index in range(4 * SAMPLE_RATE)])):
        [finding] = silence_findings(samples)
        assert finding.span == Span("audio_ms", 0, 4000)
        assert finding.strength == 0.99


def test_equal_length_runs_have_distinct_evidence():
    """Two gaps of the same length are told apart by their position."""
    samples = speech_like(1.0, 6000) + zeros(2.0) + speech_like(1.0, 6000) + zeros(2.0) + speech_like(1.0, 6000)
    findings = silence_findings(samples)
    assert len(findings) == 2
    assert findings[0].evidence != findings[1].evidence


RECOGNISED = {"cer": 0.01, "matched_characters": 99, "substitutions": 1, "deletions": 0, "insertions": 0}


def silence(start: int, end: int) -> Finding:
    """Build a silence finding for reconciliation tests."""
    return Finding("garbled_audio", "major", Span("audio_ms", start, end), "silence", "Quiet.", "Inspect.", 0.99)


def test_reconcile_downgrades_silence_that_cannot_hold_the_script():
    """A 98%-quiet reading cannot be right when 99 script characters were recognised."""
    whole = silence(0, 10000)
    clip = Finding("garbled_audio", "major", Span("audio_ms", 0, 10000), "clipping", "Clip.", "Inspect.", 0.88)
    findings = [whole, clip]
    signal = {"duration_ms": 10000, "silence_ratio": 0.98}
    updated = reconcile_signal_with_transcript(findings, RECOGNISED, signal)
    assert updated[0].strength == 0.5 and accepted([updated[0]], {"garbled_audio": 0.58}) == []
    assert updated[1] is clip
    assert findings[0] is whole and whole.strength == 0.99
    # A repeated utterance has a high CER but still recognises the whole script.
    repeated = {"cer": 1.24, "matched_characters": 99, "substitutions": 4, "deletions": 0, "insertions": 124}
    assert reconcile_signal_with_transcript(findings, repeated, signal)[0].strength == 0.5
    missing = {"cer": 0.5, "matched_characters": 50, "substitutions": 2, "deletions": 48, "insertions": 0}
    assert reconcile_signal_with_transcript(findings, missing, signal) == findings
    assert reconcile_signal_with_transcript(findings, {"alignment": "unavailable"}, signal) == findings
    assert reconcile_signal_with_transcript(findings, RECOGNISED, {}) == findings


def test_reconcile_keeps_real_gaps_in_short_clips():
    """A seeded gap or long trailing silence stays when the voiced time can hold the script."""
    short_script = {"cer": 0.0, "matched_characters": 30, "substitutions": 0, "deletions": 0, "insertions": 0}
    # 2 s speech, 3 s internal gap: the gap covers 60% of the clip.
    gap = [silence(2000, 5000)]
    assert reconcile_signal_with_transcript(gap, short_script, {"duration_ms": 5000, "silence_ratio": 0.6}) == gap
    # 2.5 s speech, 4 s trailing silence: 62% of the clip.
    trailing = [silence(2500, 6500)]
    assert reconcile_signal_with_transcript(trailing, short_script, {"duration_ms": 6500, "silence_ratio": 0.62}) == trailing


def test_spoken_number_loop_is_a_repetition_even_without_script_alignment():
    """Digits are spoken as words, so a looped time and temperature cannot align to the script."""
    from bodhan_eval.audio_checks import transcript_loops

    script = "| समय | तापमान |\n| --- | --- |\n| 08:30 | 21 °C |\n| 11:00 | 28 °C |"
    heard = "समय तापमान एट थर्टी ट्वेंटी वन डिग्रीज़ सेल्सियस एट थर्टी ट्वेंटी वन डिग्रीज़ सेल्सियस इलेवन ओ क्लॉक ट्वेंटी एट डिग्रीज़ सेल्सियस"
    finding = transcript_loops(script, heard)
    assert finding is not None and finding.category == "speech_repetition" and finding.strength >= 0.72
    assert heard[finding.span.start : finding.span.end] == finding.evidence
    assert "एट थर्टी ट्वेंटी वन" in finding.evidence


def test_phrase_repeated_in_the_script_is_not_a_loop_despite_asr_spelling():
    """The script says the phrase twice; the transcript spells one word differently but is not a loop."""
    from bodhan_eval.audio_checks import transcript_loops

    script = "धीमी गति से साइकिल चलाने वाला पहले शुरू करता है। तेज गति से साइकिल चलाने वाला 5 मिनट बाद निकलता है।"
    heard = "धीमी गति से साइकल चलाने वाला पहले शुरू करता है तेज़ गति से साइकल चलाने वाला फाइव मिनट बाद निकलता है"
    assert transcript_loops(script, heard) is None


def test_a_fifth_of_the_script_missing_in_one_block_is_strong_omission_evidence():
    """A middle cut removes a large contiguous block while the rest is heard cleanly."""
    script = "एक यू.एस.बी. केबल बिजली और डेटा ले जा सकती है। कनेक्टर का आकार अकेले इसकी डेटा दर की पहचान नहीं करता है।"
    heard = "एक यू एस बी केबल बिजली और डेटा ले जास डेटा दर की पहचान नहीं करता है"
    _, findings = speech_findings(script, heard, 6000)
    [omission] = [finding for finding in findings if finding.category == "speech_omission"]
    assert omission.strength == 0.86
    _, clean = speech_findings(script, script, 6000)
    assert not [finding for finding in clean if finding.category == "speech_omission"]


def test_loop_search_stays_fast_on_a_long_passage():
    """A 2,000-word script with a repeated 30-word stretch is checked well within a second or two."""
    import random
    import time

    from bodhan_eval.audio_checks import transcript_loops

    generator = random.Random(1)
    vocabulary = ["पानी", "गरम", "करें", "धीरे", "हिलाएँ", "कक्षा", "मिनट", "बाद", "फिर", "जांचें", "रेखा", "कोण"]
    words = []
    for index in range(2000):
        words.append(generator.choice(vocabulary))
        if index % 90 == 0:
            words += "ध्यान से पढ़ें और उत्तर लिखें".split()
    script = " ".join(words)
    started = time.monotonic()
    assert transcript_loops(script, script) is None
    looped = transcript_loops(script, script + " " + " ".join(words[100:130]))
    assert time.monotonic() - started < 5
    assert looped is not None and "30-word" in looped.detail
