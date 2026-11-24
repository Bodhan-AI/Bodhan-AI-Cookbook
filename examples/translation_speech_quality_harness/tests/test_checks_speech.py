"""Speech check: chunking, audio splitting, transcript comparison, orchestration with fakes."""

import asyncio
from dataclasses import dataclass

import numpy as np

from bodhan_eval.checks.audio import load_audio, spoken_chars
from bodhan_eval.checks.base import Flag
from bodhan_eval.checks.speech import (
    SpeechSegment,
    check_speech,
    chunk_text,
    compare_transcript,
    split_audio,
    synthesize,
    to_wav,
)
from bodhan_eval.languages import load_languages
from bodhan_eval.params import Params

L = load_languages()
P = Params.defaults()
SR = 24000

TEXT = (
    "भारत एक विशाल देश है जहाँ अनेक भाषाएँ बोली जाती हैं। "
    "यहाँ की संस्कृति बहुत पुरानी और समृद्ध मानी जाती है। "
    "हर राज्य का अपना अलग खान पान और पहनावा होता है।"
)
S1, S2, S3 = [s.strip() + "।" for s in TEXT.split("।") if s.strip()]


def cats(res):
    return sorted(f.category for f in res.flags)


def plain(t: str) -> str:
    return t.replace("।", "")


# --- chunking ----------------------------------------------------------------------------


def test_chunk_text_respects_budget_and_covers_text():
    long = " ".join([TEXT] * 6)
    chunks = chunk_text(long, chars_per_second=13.0, max_s=10.0)
    assert len(chunks) > 1
    assert all(spoken_chars(long[s:e]) <= 130 for s, e in chunks)
    joined = "".join(long[s:e] for s, e in chunks)
    assert spoken_chars(joined) == spoken_chars(long)  # nothing lost between chunks


def test_chunk_text_splits_overlong_sentence_at_space():
    sent = " ".join(["शब्द"] * 80) + "।"
    chunks = chunk_text(sent, 13.0, max_s=5.0)
    assert len(chunks) > 1 and all(spoken_chars(sent[s:e]) <= 65 for s, e in chunks)


def test_split_audio_cuts_in_pauses_under_limit():
    tone = 0.3 * np.sin(2 * np.pi * 200 * np.arange(SR * 12) / SR).astype(np.float32)
    gap = np.zeros(SR // 2, np.float32)
    x = np.concatenate([tone, gap, tone, gap, tone])  # ~37 s with pauses at ~12 s and ~24.5 s
    ranges = split_audio(x, SR, max_s=30.0)
    assert len(ranges) == 2 and ranges[0][0] == 0 and ranges[-1][1] == len(x)
    assert all((b - a) / SR <= 30.0 for a, b in ranges)
    cut = ranges[0][1]
    assert abs(x[cut]) < 1e-6  # landed in a silent pause


# --- transcript comparison -------------------------------------------------------------


def test_clean_transcript_no_flags():
    res = compare_transcript(TEXT, plain(TEXT), P)
    assert res.flags == [] and res.signals["wer"] == 0.0


def test_scattered_substitutions_are_asr_noise():
    asr = plain(TEXT).replace("विशाल", "विषाल").replace("पुरानी", "पूरानी")
    assert compare_transcript(TEXT, asr, P).flags == []


def test_missing_middle_sentence_is_omission():
    res = compare_transcript(TEXT, plain(f"{S1} {S3}"), P)
    assert cats(res) == ["speech_omission"]
    f = res.flags[0]
    assert f.details["kind"] == "omission"
    assert TEXT[f.span[0] : f.span[1]] == S2.rstrip("।")


def test_cut_off_ending_is_truncation():
    res = compare_transcript(TEXT, plain(f"{S1} {S2}"), P, duration=10.0)
    assert cats(res) == ["speech_omission"]
    f = res.flags[0]
    assert f.details["kind"] == "truncation" and f.confidence >= 0.7
    assert f.span[1] == len(TEXT) - 1  # up to the last word (before the danda)
    assert f.time_span is not None and f.time_span[1] <= 10.0


def test_numbers_read_as_words_are_not_errors():
    text = "इस पुल की लंबाई 120 मीटर है और यह 1998 में बना था।"
    asr = "इस पुल की लंबाई एक सौ बीस मीटर है और यह उन्नीस सौ अट्ठानवे में बना था"
    assert compare_transcript(text, asr, P).flags == []


def test_repeated_phrase_is_repetition():
    asr = plain(TEXT).replace("मानी जाती है", "मानी जाती है मानी जाती है मानी जाती है", 1)
    res = compare_transcript(TEXT, asr, P)
    assert cats(res) == ["speech_repetition"]
    f = res.flags[0]
    assert f.details["times"] == 3 and f.severity == "major"
    assert "मानी" in TEXT[f.span[0] : f.span[1]]


def test_repetition_present_in_text_is_fine():
    text = "धीरे धीरे धीरे चलो। अब रुको।"
    assert compare_transcript(text, "धीरे धीरे धीरे चलो अब रुको", P).flags == []


def test_garbled_stretch_low_alone_high_with_audio_backing():
    asr = plain(S1) + " कल मछली हवा पत्थर नीला घड़ी बादल सड़क " + plain(S3)
    alone = compare_transcript(TEXT, asr, P)
    assert cats(alone) == ["speech_garbled"] and alone.flags[0].confidence == 0.3
    s2 = (TEXT.index(S2), TEXT.index(S2) + len(S2))
    noise = Flag("speech_garbled", "major", "audio", 0.7, s2, "noise")
    backed = compare_transcript(TEXT, asr, P, audio_flags=[noise])
    assert backed.flags[0].confidence == 0.7 and backed.flags[0].details["audio_backed"]


def test_offset_shifts_spans_and_only_last_segment_truncates():
    res = compare_transcript(S1 + " " + S2, plain(S1), P, offset=100, is_last=False)
    f = res.flags[0]
    assert f.details["kind"] == "omission" and f.span[0] > 100


# --- orchestration ---------------------------------------------------------------------


@dataclass
class _Wav:
    wav: bytes
    cached: bool = False


@dataclass
class _Txt:
    text: str
    cached: bool = False


class FakeSpeech:
    """Speak: 1 s of tone per call, tagged by call order. Transcribe: lookup by audio length."""

    def __init__(self, transcripts=None):
        self.spoken, self.transcribed, self.transcripts = [], [], transcripts or {}

    async def speak(self, text, lang, voice, style=None):
        self.spoken.append(text)
        n = len(self.spoken)
        x = 0.2 * np.sin(2 * np.pi * 200 * np.arange(SR * n) / SR).astype(np.float32)
        return _Wav(to_wav(x, SR))

    async def transcribe(self, wav, language):
        x, sr = load_audio(wav)
        self.transcribed.append((round(len(x) / sr), language))
        if isinstance(self.transcripts, list):  # in call order
            return _Txt(self.transcripts[len(self.transcribed) - 1])
        return _Txt(self.transcripts.get(round(len(x) / sr), ""))


def test_synthesize_then_check_per_segment():
    lang = L["hi"]
    fake = FakeSpeech()

    async def go():
        segs = await synthesize(fake, TEXT, lang, "Kavya", max_s=4.0)
        # chunk k is k+1 seconds long; transcript drops the whole last chunk
        fake.transcripts = {
            k + 1: plain(TEXT[s:e]) for k, (s, e) in enumerate(x.text_span for x in segs)
        }
        fake.transcripts[len(segs)] = ""
        return segs, await check_speech(fake, TEXT, lang, P, segs)

    segs, res = asyncio.run(go())
    assert len(segs) == 3 and fake.spoken == [TEXT[s:e] for s, e in (x.text_span for x in segs)]
    assert all(code == lang.speech_code for _, code in fake.transcribed)
    assert cats(res) == ["speech_omission"]
    assert res.flags[0].details["kind"] == "truncation"
    assert res.flags[0].span[0] >= segs[-1].text_span[0]
    assert len(res.signals["segments"]) == 3


def test_long_supplied_audio_is_split_for_transcription():
    x = 0.2 * np.sin(2 * np.pi * 200 * np.arange(SR * 50) / SR).astype(np.float32)
    fake = FakeSpeech([plain(S1 + " " + S2), plain(S3)])
    res = asyncio.run(check_speech(fake, TEXT, L["hi"], P, [SpeechSegment(to_wav(x, SR))]))
    lengths = [d for d, _ in fake.transcribed]
    assert len(lengths) == 2 and sum(lengths) == 50 and max(lengths) <= 30
    assert res.flags == [] and res.signals["wer"] == 0.0


def test_number_inside_missing_stretch_does_not_split_it():
    text = "नदी पहाड़ों से निकलकर समुद्र में मिलती है और इसके किनारे 40 करोड़ से अधिक लोग रहते हैं।"
    asr = "नदी पहाड़ों से"  # everything after the third word is cut off, including "40"
    res = compare_transcript(text, asr, P)
    assert [f.details["kind"] for f in res.flags] == ["truncation"]


def test_dotted_acronym_is_soft():
    text = "डार्पा को पहले ए.आर.पी.ए कहा जाता था।"
    assert compare_transcript(text, "डार्पा को पहले ARPA कहा जाता था", P).flags == []
