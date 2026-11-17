import io

import numpy as np
import soundfile as sf
from conftest import to_wav, tone

from bodhan_eval.clients import split_for_asr
from bodhan_eval.speechchecks import analyze_audio, audio_candidates, speech_rate_outliers, transcript_candidates

TEXT = "पानी समुद्र तल पर सौ डिग्री पर उबलता है और ऊंचाई पर कम तापमान पर उबलता है"


def test_clean_audio_has_no_signal_flags():
    audio = analyze_audio(to_wav(tone(3.0)))
    assert not audio["empty"] and audio["longest_internal_silence_s"] < 0.2
    assert audio_candidates(audio, TEXT) == []


def test_long_internal_silence_and_noise_are_garbled():
    sr = 24000
    x = np.concatenate([tone(1.0), np.zeros(2 * sr, "float32"), tone(1.0)])
    [c] = audio_candidates(analyze_audio(to_wav(x)), TEXT)
    assert c.category == "speech_garbled" and c.evidence["at_s"] >= 0.9

    noise = np.random.default_rng(0).normal(0, 0.3, sr).astype("float32").clip(-1, 1)
    y = np.concatenate([tone(1.0), noise, tone(1.0)])
    assert any("noise" in c.evidence["problem"] for c in audio_candidates(analyze_audio(to_wav(y)), TEXT))


def test_silent_audio_is_an_omission():
    [c] = audio_candidates(analyze_audio(to_wav(np.zeros(24000, "float32"))), TEXT)
    assert c.category == "speech_omission" and c.severity == "critical"


def test_transcript_alignment_finds_omission_with_span():
    heard = "पानी समुद्र तल पर सौ डिग्री पर उबलता है"
    cands, metrics = transcript_candidates(TEXT, heard)
    [c] = [c for c in cands if c.category == "speech_omission"]
    assert "ऊंचाई" in TEXT[slice(*c.span)] and c.evidence["truncated_end"]
    assert metrics["cer"] > 0.3


def test_transcript_alignment_finds_repetition():
    heard = "पानी समुद्र तल पर सौ डिग्री पर सौ डिग्री पर उबलता है और ऊंचाई पर कम तापमान पर उबलता है"
    cands, _ = transcript_candidates(TEXT, heard)
    [c] = [c for c in cands if c.category == "speech_repetition"]
    assert "डिग्री" in TEXT[slice(*c.span)]


def test_numbers_spoken_as_words_are_not_errors():
    # Seen live: Indic-Transcribe renders "100" as "वन हंड्रेड".
    cands, _ = transcript_candidates("पानी 100 डिग्री पर उबलता है", "पानी वन हंड्रेड डिग्री पर उबलता है")
    assert cands == []


def test_truncation_next_to_a_number_is_still_an_omission():
    # Seen live (civ-003.mr): audio cut after "...तर राज्य"; the missing tail contains "२५०".
    text = "लोकसभेत जास्तीत जास्त ५५२ सदस्य असू शकतात तर राज्यसभेत २५० पर्यंत सदस्य असू शकतात."
    heard = "लोकसभेत जास्तीत जास्त पाचशे बावन्न सदस्य असू शकतात तर राज्य"
    [c] = [c for c in transcript_candidates(text, heard)[0] if c.category == "speech_omission"]
    assert c.score >= 0.75 and c.evidence["truncated_end"]  # a third of the sentence lost at the end is strong evidence
    assert "पर्यंत" in text[slice(*c.span)] and "लोकसभेत" not in text[slice(*c.span)]


def test_dropped_heading_before_a_numbered_list_is_an_omission():
    # Seen live (md-002.ml): the heading segment was dropped; the list marker "1." is spoken as "വൺ".
    text = "## തൈ നടുന്നതിനുള്ള ഘട്ടങ്ങൾ\n\n1. ഏകദേശം 60 സെൻ്റീമീറ്റർ ആഴത്തിൽ ഒരു കുഴിയെടുക്കുക."
    heard = "വൺ ഏകദേശം സിക്സ്റ്റി സെൻ്റീമീറ്റർ ആഴത്തിൽ ഒരു കുഴിയെടുക്കുക"
    [c] = [c for c in transcript_candidates(text, heard)[0] if c.category == "speech_omission"]
    assert c.score >= 0.5 and "ഘട്ടങ്ങൾ" in text[slice(*c.span)]


def test_repeated_heading_next_to_a_list_marker_is_repetition():
    # Seen live (md-002.pa): heading spoken twice, adjacent to "1." spoken as "ਵਨ".
    text = "## ਬੂਟਾ ਲਗਾਉਣ ਦੇ ਕਦਮ\n\n1. ਲਗਭਗ 60 ਸੈਂਟੀਮੀਟਰ ਡੂੰਘਾ ਟੋਆ ਪੁੱਟੋ।"
    heard = "ਬੂਟਾ ਲਗਾਉਣ ਦੇ ਕਦਮ ਬੂਟਾ ਲਗਾਉਣ ਦੇ ਕਦਮ ਵਨ ਲਗਭਗ ਸਿਕਸਟੀ ਸੈਂਟੀਮੀਟਰ ਡੂੰਘਾ ਟੋਆ ਪੁੱਟੋ"
    cands, _ = transcript_candidates(text, heard)
    [c] = [c for c in cands if c.category == "speech_repetition"]
    assert c.score >= 0.5 and "ਕਦਮ" in text[slice(*c.span)]
    assert not any(c.category == "speech_omission" and c.score >= 0.4 for c in cands)


def test_looped_spoken_math_is_repetition():
    # Seen live (math-003.or): the spoken form of "$b = 4$ ... $c = 5$" was looped; it never exists as written words.
    text = "ପାଇଥାଗୋରାସ୍ ପ୍ରମେୟ ଅନୁସାରେ, $a^2 + b^2 = c^2$ । ଯଦି $a = 3$ ଏବଂ $b = 4$ ହୁଏ, ତେବେ $c = 5$ ହେବ ।"
    heard = ("ପାଇଥାଗୋରାସ୍ ପ୍ରମେୟ ଅନୁସାରେ ଏ ଟୁ ଟୁ ପ୍ଲସ୍ ବି ଟୁ ଇକ୍ୱାଲ୍ ସି ଟୁ ଟୁ ଯଦି ଏ ଇକ୍ୱାଲ୍ ଟୁ ଥ୍ରୀ ଏବଂ ବି ଇକ୍ୱାଲ୍ ଟୁ ଫୋର୍ ହୁଏ ତେବେ ସି "
             "ଇକ୍ୱାଲ୍ ଟୁ ଫାଇଭ୍ ହେବ ଟୁ ଫୋର୍ ହୁଏ ତେବେ ସି ଇକ୍ୱାଲ୍ ଟୁ ଫାଇଭ୍ ହେବ")
    assert any(c.category == "speech_repetition" and c.score >= 0.5 for c in transcript_candidates(text, heard)[0])


def test_repeated_table_header_with_spelling_variant_is_repetition():
    # Seen live (md-001.or): header row spoken twice; ASR writes ଵ (U+0B35) where the text has ୱ (U+0B71).
    text = "ଟେବୁଲ୍ ପ୍ରାୟ ଦୈନିକ ପରିମାଣ ଦର୍ଶାଏ।\n\n| ପୋଷକ ତତ୍ତ୍ୱ | ପରିମାଣ |\n|---|---|\n| ପ୍ରୋଟିନ୍ | 50 g |"
    heard = "ଟେବୁଲ୍ ପ୍ରାୟ ଦୈନିକ ପରିମାଣ ଦର୍ଶାଏ ପୋଷକ ତତ୍ତ୍ଵ ପରିମାଣ ପୋଷକ ତତ୍ତ୍ଵ ପରିମାଣ ପ୍ରୋଟିନ୍ ଫିଫ୍ଟି ଗ୍ରାମ୍ସ"
    assert any(c.category == "speech_repetition" and c.score >= 0.5 for c in transcript_candidates(text, heard)[0])


def test_math_read_aloud_is_not_garbled():
    # Seen live (math-004.ta): LaTeX is spoken as English words in Tamil script.
    text = "$\\frac{x}{2} + 3 = 7$ என்பதைத் தீர்க்க, இருபுறமும் 3-ஐக் கழித்து, பின்னர் 2-ஆல் பெருக்கினால் $x = 8$ கிடைக்கும்."
    heard = "எக்ஸ் டூ ப்ளஸ் த்ரீ ஈக்குவல்ஸ் செவன் என்பதைத் தீர்க்க இருபுறமும் த்ரீ மைனஸ் கழித்து பின்னர் டூ மைனஸ் ஆல் பெருக்கினால் எக்ஸ் ஈக்குவல்ஸ் எயிட் கிடைக்கும்"
    cands, metrics = transcript_candidates(text, heard)
    assert not any(c.category == "speech_garbled" and c.score >= 0.5 for c in cands)
    assert metrics["word_coverage"] >= 0.8


def test_unrelated_transcript_is_garbled():
    cands, _ = transcript_candidates(TEXT, "कुछ और ही बोला गया जो बिल्कुल अलग था")
    assert any(c.category in {"speech_garbled", "speech_omission"} and c.score >= 0.5 for c in cands)


def test_small_asr_spelling_noise_is_not_an_error_candidate_above_default_threshold():
    heard = TEXT.replace("तापमान", "तापमन").replace("ऊंचाई", "उंचाई")
    cands, _ = transcript_candidates(TEXT, heard)
    assert all(c.score < 0.4 for c in cands)


def test_speech_rate_outliers_flag_too_short_and_too_long_audio():
    rows = [(f"i{k}", TEXT, 6.0 + 0.1 * k) for k in range(10)] + [("short", TEXT, 1.5), ("long", TEXT, 30.0)]
    out = speech_rate_outliers(rows)
    assert out["short"][0].category == "speech_omission"
    assert out["long"][0].category == "speech_garbled"
    assert not any(k.startswith("i") for k in out)


def test_voice_comparison_ranks_configs(tmp_path):
    from conftest import FakeBodhan

    from bodhan_eval.voices import evaluate_config, write_report

    class Fake(FakeBodhan):
        def transcribe(self, wav, lang):  # the "Amit" config drops the second half of every sentence
            return {"text": TEXT if self.voice != "Amit" else " ".join(TEXT.split()[:6]), "cost": 0.0}

        def speak(self, text, lang, voice=None, style=None):
            self.voice = voice
            return super().speak(text, lang)

    items = [{"id": f"i{k}", "translation": TEXT} for k in range(3)]
    b = Fake()
    results = [evaluate_config(b, items, "hi", v, None) for v in ("Kavya", "Amit")]
    write_report(tmp_path, "hi", results)
    md = (tmp_path / "voices.md").read_text(encoding="utf-8")
    assert md.index("| 1 | Kavya") < md.index("| 2 | Amit")
    assert results[1]["flags_by_category"].get("speech_omission") == 3


def test_long_audio_is_split_below_the_30s_window():
    sr = 24000
    x = np.concatenate([np.concatenate([tone(4.0), np.zeros(int(0.4 * sr), "float32")]) for _ in range(16)])  # ~70 s
    chunks = split_for_asr(to_wav(x, sr))
    durations = [sf.info(io.BytesIO(c)).duration for c in chunks]
    assert len(chunks) >= 3 and max(durations) <= 28.0 + 1e-6
    assert abs(sum(durations) - len(x) / sr) < 0.05
