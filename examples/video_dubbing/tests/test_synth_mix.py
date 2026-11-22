"""Timing and QA logic: CER and take classification, fitting, placement, ducking, subtitles, voices, report."""

import numpy as np
import pytest

from bodhan_dub import audio, mix, voices
from bodhan_dub.repair import Unit
from bodhan_dub.report import language_report
from bodhan_dub.synth import (
    MAX_TEMPO,
    DubLine,
    SpokenSentence,
    Synthesizer,
    Take,
    pack_requests,
    split_indic_sentences,
)
from bodhan_dub.translate import LanguageResult, TranslatedUnit
from bodhan_dub.ttsqa import judge_clip, wildcard_cer
from bodhan_dub.vad import SAMPLE_RATE

# ---------------------------------------------------------------------------
# QA helpers
# ---------------------------------------------------------------------------


def test_number_aware_cer_accepts_numbers_and_english_words_spoken_differently():
    ref = "180 डिग्री से बड़ा कोण एक reflex angle होता है।"
    heard = "वन हंड्रेड एंड एटी डिग्री से बड़ा कोण एक रिफ्लेक्स एंगल होता है"
    assert wildcard_cer(ref, heard) == 0.0
    assert wildcard_cer("कोण १८० है", "कोण 180 है") == 0.0
    assert wildcard_cer("कखगघ", "कखचघ") == 0.25  # native script is compared letter by letter
    assert wildcard_cer("word", "different") == 0.0  # Latin words are wildcards by design


@pytest.mark.parametrize("ref, heard, expected", [
    ("अपनी नोटबुक खोलें और शीर्षक लिखें।", "अपनी नोटबुक खोलें और शीर्षक लिखें", ""),
    ("एक अधिक कोण 90 और 180 डिग्री के बीच होता है, और एक ऋजु कोण 180 डिग्री होता है।",
     "एक अधिक कोण नाइंटी और हंड्रेड एंड एटी डिग्री के बीच होता है और एक रिजू कोण वन हंड्रेड एंड एटी डिग्री के बीच "
     "होता है और एक रिजू कोण वन हंड्रेड एंड एटी डिग्री के बीच होता है", "repetition"),
    ("यहाँ बिंदु A, O और B संरेख हैं और बिंदु C नहीं है।", "यहाँ बिंदु ए ओ बिंदु सी नहीं है", "omission"),
    ("यह एक वाक्य है", "वह दो शब्द थे भाई", "mismatch"),
])
def test_clip_judgement_on_real_failure_shapes(ref, heard, expected):
    assert judge_clip(ref, heard, 4.0).issue == expected


def test_numbers_said_twice_legitimately_are_not_a_loop():
    ref = "180 डिग्री और फिर से 180 डिग्री"
    heard = "वन हंड्रेड एंड एटी डिग्री और फिर से वन हंड्रेड एंड एटी डिग्री"
    assert judge_clip(ref, heard, 4.0).issue == ""


def test_requests_pack_two_short_sentences():
    sents = ["पहला वाक्य है।", "दूसरा वाक्य है।", "तीसरा वाक्य है।", "क" * 230]
    assert [len(g) for g in pack_requests(sents)] == [2, 1, 1]


def test_indic_sentence_split_uses_danda_and_merges_fragments():
    assert split_indic_sentences("पहला वाक्य यहाँ है। दूसरा वाक्य भी है। हाँ।") == [
        "पहला वाक्य यहाँ है।", "दूसरा वाक्य भी है। हाँ।"]


# ---------------------------------------------------------------------------
# fitting
# ---------------------------------------------------------------------------


def line_with(natural_s, budget_s, start=10.0, end=14.0, rate=24_000):
    unit = TranslatedUnit(id="u0000", start=start, end=end, source_text="Hello.", translation="नमस्ते।")
    s = SpokenSentence(text="नमस्ते।", takes=[Take("whole", duration_s=natural_s, cer=0.0)],
                       samples=np.zeros(int(natural_s * rate), dtype=np.float32), rate=rate)
    return DubLine(unit=unit, voice="Kavya", gender="female", sentences=[s], budget_s=budget_s)


class NoCondense(Synthesizer):
    def __init__(self):
        self.llm = None  # no LLM: condensing is impossible, so fitting stops at the 1.2x stretch

    def condense(self, english, ratio, lang):
        return "", "no LLM configured"


SRC = Unit(id="u0000", start=10.0, end=14.0, text="Hello.", raw_text="hello", source_ids=["s0000"])


def test_fits_without_stretch_when_there_is_room():
    line = line_with(3.5, 4.0)
    NoCondense().fit(line, SRC, "hi")
    assert line.tempo == 1.0 and not line.notes


def test_gentle_stretch_up_to_the_limit():
    line = line_with(4.4, 4.0)
    NoCondense().fit(line, SRC, "hi")
    assert line.tempo == pytest.approx(1.1)


def test_never_stretches_past_the_limit_and_reports_the_overrun():
    line = line_with(6.0, 4.0)
    NoCondense().fit(line, SRC, "hi")
    assert line.tempo == MAX_TEMPO
    assert line.condensed and line.condensed[0]["result"].startswith("no acceptable shorter English")
    assert "past its slot" in line.notes[0]


# ---------------------------------------------------------------------------
# placement, ducking, subtitles
# ---------------------------------------------------------------------------


def test_placement_never_overlaps_and_records_the_shift():
    a, b = line_with(5.0, 4.0, start=0.0, end=4.0), line_with(2.0, 3.0, start=4.5, end=7.0)
    mix.place([a, b], video_s=30.0)
    assert (a.placed_start, a.placed_end) == (0.0, 5.0)
    assert b.placed_start == pytest.approx(5.08)  # pushed back by the long first line, plus a small gap


def test_duck_envelope_ramps_down_inside_intervals_only():
    env = mix.duck_envelope(10 * 1000, [(2.0, 4.0)], rate=1000)
    assert env[0] == 1.0 and env[9999] == 1.0
    assert env[3000] == pytest.approx(mix.DUCK_GAIN)
    assert 0.0 < env[1900] < 1.0  # ramp, not a click


def test_srt_cues_follow_sentence_audio_and_long_text_is_split():
    line = line_with(4.0, 4.0, start=2.0, end=6.0)
    line.sentences[0].text = "शब्द " * 40
    mix.place([line], video_s=30.0)
    cues = mix.subtitle_cues([line])
    assert len(cues) >= 3 and all(len(text) <= mix.MAX_CUE_CHARS for _, _, text in cues)
    assert cues[0][0] == pytest.approx(2.0) and cues[-1][1] == pytest.approx(6.0)
    assert mix._ts(3725.5) == "01:02:05,500"


# ---------------------------------------------------------------------------
# audio helpers
# ---------------------------------------------------------------------------


def test_trim_silence_keeps_only_the_speech():
    rate = 16_000
    tone = 0.3 * np.sin(np.linspace(0, 2 * np.pi * 300, rate)).astype(np.float32)
    padded = np.concatenate([np.zeros(rate, np.float32), tone, np.zeros(rate, np.float32)])
    assert len(audio.trim_silence(padded, rate)) / rate == pytest.approx(1.0, abs=0.08)


def test_loudness_normalisation_hits_target():
    rate = audio.MIX_RATE
    t = np.arange(3 * rate) / rate
    tone = (0.05 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    _, lufs = audio.normalize_loudness(tone, -16.0, rate)
    assert lufs == pytest.approx(-16.0, abs=0.5)


# ---------------------------------------------------------------------------
# voices
# ---------------------------------------------------------------------------


def test_pitch_estimate_and_gender_hysteresis():
    t = np.arange(2 * SAMPLE_RATE) / SAMPLE_RATE
    low = (0.3 * np.sin(2 * np.pi * 120 * t)).astype(np.float32)
    assert voices.median_pitch(low, 0.0, 2.0) == pytest.approx(120, rel=0.05)
    assert voices.assign_genders([220, 180, 230, None, 120]) == ["female", "female", "female", "female", "male"]


def test_voice_config_validation(tmp_path):
    good = tmp_path / "v.yaml"
    good.write_text("hi: Kavya\nta: {male: Arun}\nstyle: educational lecture\n")
    cfg = voices.load_voice_config(good)
    assert voices.voice_for("hi", "male", cfg) == "Kavya" and voices.voice_for("ta", "female", cfg) == "Arun"
    assert voices.voice_for("bn", "male", cfg) == "Sourav"
    bad = tmp_path / "bad.yaml"
    bad.write_text("hi: Nobody\n")
    with pytest.raises(voices.VoiceConfigError, match="unknown voice"):
        voices.load_voice_config(bad)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def test_report_counts_and_explains_misses():
    ok = line_with(3.0, 4.0, start=0.0, end=4.0)
    late = line_with(2.0, 3.0, start=4.0, end=7.0)
    mix.place([ok, late], video_s=30.0)
    late.placed_start = 4.5  # pretend an overrun pushed it 500 ms
    skipped = DubLine(unit=TranslatedUnit(id="u0002", start=8, end=9, source_text="x", status="skipped",
                                          note="wrong script"), status="skipped", notes=["not dubbed: wrong script"])
    rep = language_report("hi", LanguageResult(lang="hi"), [ok, late, skipped], input_s=30.0, output_s=30.02,
                          speech_lufs=-16.2, timings={"translate": 1.0})
    assert rep["coverage"]["dubbed"] == 2 and rep["coverage"]["skipped"][0]["id"] == "u0002"
    assert rep["sync_ms"]["within_300ms"] == 1 and rep["sync_ms"]["max"] == 500
    assert rep["total_length_ms"] == 20
    assert any(m.startswith("sync:") for m in rep["misses"]) and any("coverage" in m for m in rep["misses"])



def test_ai_label_is_always_present():
    early = [(0.5, 3.0, "पहला"), (4.0, 9.5, "दूसरा")]
    assert mix.add_ai_label(early, 20.0)[-1][2] == mix.AI_LABEL  # no room at the start: at the end
    assert mix.add_ai_label(early, 9.6)[0][2].startswith(mix.AI_LABEL + "\n")  # no room anywhere: own line
    assert mix.add_ai_label([(2.0, 3.0, "x")], 5.0)[0][2] == mix.AI_LABEL
    assert mix.add_ai_label([], 5.0) == [(0.0, 3.0, mix.AI_LABEL)]


def test_only_dubbed_lines_duck_the_original():
    dubbed = line_with(3.0, 4.0, start=1.0, end=4.0)
    skipped = DubLine(unit=TranslatedUnit(id="u0001", start=6, end=9, source_text="x", status="skipped"))
    mix.place([dubbed, skipped], video_s=30.0)
    assert mix.duck_intervals([dubbed, skipped]) == [(1.0, 4.0), (1.0, 4.0)]  # u0001 keeps its original audio


class ScriptedCondense(Synthesizer):
    """Condense returns progressively shorter English; translation and speech scale with its length."""

    def __init__(self, seconds_per_char=0.1):
        self.llm = object()  # present; condense itself is scripted below
        self.seconds_per_char = seconds_per_char
        self.asked = []
        from bodhan_dub.translate import Candidate

        outer = self

        class T:
            terms = []

            def make_candidate(self, unit, lang, kind):
                return Candidate(kind=kind, text=unit.text)

        self.translator = T()
        self._candidate = Candidate
        self.speak_text = lambda text, voice, lang: [SpokenSentence(
            text=text, takes=[Take("whole", duration_s=len(text) * outer.seconds_per_char, cer=0.0)],
            samples=np.zeros(10, np.float32))]

    def condense(self, english, ratio, lang):
        self.asked.append(round(ratio, 3))
        return english[: int(len(english) * ratio)], ""


def test_condense_rounds_ask_for_progressively_less_until_it_fits():
    src = Unit(id="u0000", start=0.0, end=4.0, text="x" * 100, raw_text="x", source_ids=["s0000"])
    line = line_with(10.0, 4.0)
    syn = ScriptedCondense()
    syn.fit(line, src, "hi")
    assert line.condensed[0]["result"] == "accepted"
    assert line.natural_s <= line.budget_s * MAX_TEMPO + 1e-9 and not line.notes
    assert syn.asked == sorted(syn.asked, reverse=True)  # never asks for the same thing twice
