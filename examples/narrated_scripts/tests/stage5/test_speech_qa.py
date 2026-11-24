import io
import json
import wave
from dataclasses import replace

import httpx
import pytest
from PIL import Image

from backend import runner
from backend.audio.speech_qa import compare_speech, verify_chunk, verify_script
from backend.audio.transcribe import IndicTranscribeProvider, Transcription
from backend.inference import CUDA_OOM, Inference
from backend.models import (
    ChunkSpeechQA, NarrationScript, ScriptChunk, ScriptSection, SpeechCandidate, SpeechQAAttempt, SpokenNumber,
    TargetSpeech,
)
from backend.report import AttemptLog
from backend.translation.provider import ModelTranslation
from bodhan_lesson import main


def script_chunk(text, number=1, policy="paragraph"):
    return ScriptChunk(f"s001-c{number:02d}", "s001", ("p001-b001",), ("p001-b001",), text, policy)


def candidate(chunk, attempt_id):
    return SpeechCandidate(
        chunk.id, chunk.section_id, "Arun", len(chunk.text), "ok", (attempt_id,), attempt_id,
        f"en/work/audio/{attempt_id}.wav", "wav", 2.5,
    )


def unexpected(*args):
    pytest.fail(f"Unexpected model call: {args}")


def test_exact_match_has_zero_cer_and_keeps_raw_text():
    text = "यह एक कोण है।"
    result = compare_speech(text, text, "hi")
    assert result.reference == result.transcript == text
    assert result.normalized_reference == result.normalized_transcript == "यह एक कोण है"
    assert result.cer == result.insertions == result.deletions == result.substitutions == 0
    assert result.edits == result.signals == ()


def test_nfc_whitespace_punctuation_and_case_compares_equal():
    result = compare_speech("  क़िला, CAFÉ।\n यहाँ है  ", "क़िला CAFE\u0301 यहाँ है!")
    assert result.cer == 0
    assert result.normalized_reference == "क़िला café यहाँ है"
    assert result.reference.startswith("  ") and result.transcript.startswith("क़")


def test_marks_numbers_and_negation_remain_meaningful():
    result = compare_speech("ऊँचाई १२.५ नहीं है।", "ऊचाई १२५ सही है।", "hi")
    assert "ँ" in result.normalized_reference
    assert "१२.५" in result.normalized_reference
    assert set(result.signals) >= {"number_change", "negation_change"}
    assert result.cer > 0


@pytest.mark.parametrize(("reference", "transcript"), [
    ("CHAPTER 6", "சாப்டர் சிக்ஸ்"),
    ("6.1 அறிமுகம்", "சிக்ஸ் பாயிண்ட் ஒன் அறிமுகம்"),
    ("90 பாகை", "நைன்ட்டி டிகிரீஸ்"),
    ("கோணம் AOD", "ஆங்கிள் ஏஓடி"),
    ("கோணம் ABD மற்றும் கோணம் DBC", "கோணம் ஏ பி டி மற்றும் கோணம் டி பி சி"),
    ("கதிர் BA மற்றும் கதிர் BC", "கதிர் பி ஏ மற்றும் கதிர் பி சி"),
    ("புள்ளி B.", "புள்ளி பி"),
    ("6.1 அறிமுகம்", "ஆறு புள்ளி ஒன்று அறிமுகம்"),
    ("ஆறு புள்ளி ஒன்று அறிமுகம்", "6.1 அறிமுகம்"),
    ("ஆறு புள்ளி ஒன்று அறிமுகம்", "ஆறு புள்ளி ஒன்னு அறிமுகம்"),
    ("எதிர்மறை பன்னிரண்டு புள்ளி பூஜ்ஜியம் ஐந்து", "−12.05"),
    ("எதிர்மறை பூஜ்ஜியம் புள்ளி ஐந்து", "-.5"),
    ("தொண்ணூறு °", "90°"),
    ("ஒரு லட்சத்து இருபத்துமூன்று ஆயிரத்து நானூற்று ஐம்பத்தாறு", "1,23,456"),
    ("ஒரு கோடியே ஓராயிரத்து ஒன்று", "10001001"),
    ("s என்பது 180 டிகிரிக்குச் சமம்", "எஸ் என்பது ஒன் ஹண்ட்ரட் அண்ட் எயிட்டி டிகிரிக்கு சமம்"),
])
def test_tamil_qa_compares_recognized_spoken_forms(reference, transcript):
    result = compare_speech(reference, transcript, "ta")
    assert result.reference == reference and result.transcript == transcript
    assert result.cer == 0 and result.signals == ()


def test_tamil_qa_keeps_different_values_labels_and_omitted_figures_suspect():
    assert "number_change" in compare_speech("6.1 அறிமுகம்", "சிக்ஸ் பாயிண்ட் டூ அறிமுகம்", "ta").signals
    assert "label_change" in compare_speech("கோணம் AOD", "ஆங்கிள் பிஓடி", "ta").signals
    assert "label_change" in compare_speech("கதிர் BD", "கதிர் பி பி", "ta").signals
    assert "label_change" in compare_speech("புள்ளி B", "புள்ளி டி", "ta").signals
    assert "number_change" in compare_speech("படம் 6.2 ஐப் பார்க்கவும்", "படம் பார்க்கவும்", "ta").signals
    assert compare_speech("90 பாகை", "நைன்ட்டி டாலர்ஸ் சர்க்கிள்", "ta").cer > 0.15


def test_tamil_qa_flags_english_pronunciation_at_the_same_numeric_value():
    result = compare_speech("ஆறு புள்ளி ஒன்று அறிமுகம்", "சிக்ஸ் பாயிண்ட் ஒன் அறிமுகம்", "ta")
    assert result.cer == 0
    assert result.signals == ("number_language_change",)
    assert "number_change" in compare_speech("இருபத்தைந்து", "26", "ta").signals


@pytest.mark.parametrize(("language", "words", "digits", "different"), [
    ("hi", "पाँच दशमलव एक शून्य", "५.१०", "पाँच दशमलव एक दो"),
    ("bn", "পাঁচ দশমিক এক শূন্য", "৫.১০", "পাঁচ দশমিক এক দুই"),
])
def test_qa_compares_local_number_words_by_checked_value(language, words, digits, different):
    numbers = (SpokenNumber("5.10", words),)
    same = compare_speech(words, digits, language, spoken_numbers=numbers)
    assert same.cer == 0 and same.signals == ()
    assert same.normalized_reference == same.normalized_transcript == "5.10"

    wrong = compare_speech(words, different, language, spoken_numbers=numbers)
    assert "number_change" in wrong.signals
    english = compare_speech(words, "five point one zero", language, spoken_numbers=numbers)
    assert "number_language_change" in english.signals


def test_qa_accepts_a_hindi_decimal_with_alternate_number_spelling():
    number = SpokenNumber("6.1", "छः दशमलव एक")
    result = compare_speech(number.text, "छह दशमलव एक", "hi", spoken_numbers=(number,))
    assert result.cer == 0 and result.signals == ()


@pytest.mark.parametrize("transcript", ["৫টি", "পাঁচটি"])
def test_qa_accepts_bengali_number_with_attached_classifier(transcript):
    number = SpokenNumber("5", "পাঁচ")
    result = compare_speech("পাঁচ টি", transcript, "bn", spoken_numbers=(number,))
    assert result.cer == 0 and result.signals == ()


def test_tamil_omitted_aside_is_preserved_in_the_replacement_speech():
    chunk = script_chunk("இதைக் கவனியுங்கள் (பின் இணைப்பு 1 ஐப் பார்க்கவும்).")
    original = candidate(chunk, "a000001")
    replacement_text = "இதைக் கவனியுங்கள், பின் இணைப்பு 1 ஐப் பார்க்கவும்."
    calls = []

    def synthesize(revised, language, voice):
        assert revised.text == replacement_text and revised.id == chunk.id
        calls.append(revised)
        return candidate(revised, "a000003")

    def transcribe(clip, language):
        text = "இதைக் கவனியுங்கள்." if clip == original else replacement_text
        return Transcription(text)

    result = verify_chunk(chunk, original, "ta", synthesize, transcribe, 0.15)
    assert len(calls) == 1
    assert result.attempts[0].comparison.reference == chunk.text
    assert result.attempts[1].comparison.reference == replacement_text
    assert result.accepted_candidate_ids == ("a000003",)


def test_tamil_retry_keeps_the_passing_half_and_preserves_playback_order():
    left = "கதிர் BD."
    right = "புள்ளி B."
    original = script_chunk(f"{left} {right}")
    first = replace(original, id=f"{original.id}-p01", text=left)
    second = replace(original, id=f"{original.id}-p02", text=right)
    reviews = tuple(
        SpeechQAAttempt(phase, clip, compare_speech(text, transcript, "ta"), (), status)
        for phase, clip, text, transcript, status in (
            ("original", candidate(original, "a000001"), original.text, "கதிர் பி பி", "suspect"),
            ("split", candidate(first, "a000002"), left, "கதிர் பி பி", "suspect"),
            ("split", candidate(second, "a000003"), right, "புள்ளி பி", "passed"),
        )
    )
    old = ChunkSpeechQA(original.id, len(original.text), reviews, (), "exhausted")

    def synthesize(chunk, language, voice):
        assert chunk.id == first.id and chunk.text == "கதிர் B D."
        return candidate(chunk, "a000004")

    result = verify_chunk(original, reviews[0].candidate, "ta", synthesize,
                          lambda *_: Transcription("கதிர் பி டி"), 0.15, previous=old)
    assert result.accepted_candidate_ids == ("a000004", "a000003")
    assert [clip.chunk_id for clip in result.accepted_candidates] == [first.id, second.id]


@pytest.mark.parametrize(("spoken", "signal"), [
    ("Keep going keep going. Take a breath.", "repetition"),
    ("Keep going. Take a", "omission"),
])
def test_repetition_and_omitted_ending_trigger_one_regeneration(spoken, signal):
    chunk = script_chunk("Keep going. Take a breath.")
    initial = candidate(chunk, "a000001")
    replacement = candidate(chunk, "a000003")
    calls = []

    def synthesize(current, language, voice):
        assert current == chunk and language == "en" and voice == initial.voice
        calls.append(current.id)
        return replacement

    def transcribe(current, language):
        assert language == "en"
        if current == initial:
            return Transcription(spoken, ("a000002",))
        return Transcription(chunk.text, ("a000004",))

    result = verify_chunk(chunk, initial, "en", synthesize, transcribe, threshold=1)
    original, repaired = result.attempts
    assert signal in original.comparison.signals
    assert original.comparison.edits
    assert original.transcribe_attempt_ids == ("a000002",)
    assert original.outcome == "suspect" and repaired.outcome == "passed"
    assert calls == [chunk.id]
    assert result.accepted_candidate_ids == ("a000003",)
    assert result.accepted_candidates == (replacement,)


def test_weighted_cer_and_partial_coverage_exclude_rejected_attempts():
    short = script_chunk("ten")
    long = script_chunk("a longer example", 2)
    missed = script_chunk("one two", 3)
    skipped = ScriptSection("s002", (), (), "The script skipped this section.")
    script = NarrationScript("en", None, 300, "completed", (
        ScriptSection("s001", (short, long, missed), ()),
        skipped,
    ))
    originals = tuple(candidate(chunk, f"a{index:06d}") for index, chunk in enumerate((short, long, missed), 1))
    speech = TargetSpeech("en", "Arun", "completed", originals)
    texts = {"a000001": "tan", "a000002": long.text, "a000003": "one", "a000004": ""}

    def synthesize(chunk, language, voice):
        assert chunk == missed
        return candidate(chunk, "a000004")

    result = verify_script(script, speech, synthesize,
                           lambda clip, language: Transcription(texts[clip.attempt_id]), threshold=0.34)
    section, skipped_result = result.sections
    assert result.status == "completed_with_misses"
    assert section.metrics.total_edits == 1
    assert section.metrics.cer == pytest.approx(1 / (len(short.text) + len(long.text)))
    assert section.metrics.measured_chunks == 2 and section.metrics.total_chunks == 3
    assert section.metrics.coverage == pytest.approx((len(short.text) + len(long.text)) / 26)
    assert "s001" in section.reason and missed.id in section.reason
    assert section.chunks[-1].status == "exhausted"
    assert section.chunks[-1].accepted_candidate_ids == ()
    assert section.chunks[-1].attempts[-1].comparison.cer == 1
    assert result.metrics == section.metrics
    assert skipped_result.metrics.cer is None and skipped_result.metrics.reason
    assert skipped_result.reason == skipped.reason


@pytest.mark.parametrize("split_passes", [True, False])
def test_split_is_lossless_and_only_complete_split_enters_verified_set(split_passes):
    chunk = script_chunk("Check 12.5 cm first. Then compare both angles.")
    initial = candidate(chunk, "a000001")
    scripts = {}

    def synthesize(current, language, voice):
        clip = candidate(current, f"a{len(scripts) + 2:06d}")
        assert current.source_block_ids == chunk.source_block_ids and current.unit_ids == chunk.unit_ids
        scripts[clip.attempt_id] = current
        return clip

    def transcribe(clip, language):
        current = scripts.get(clip.attempt_id)
        if current is not None and "-p" in current.id and (split_passes or current.id.endswith("01")):
            return Transcription(current.text)
        return Transcription("")

    result = verify_chunk(chunk, initial, "en", synthesize, transcribe, threshold=0.15)
    assert tuple(review.phase for review in result.attempts) == ("original", "regenerated", "split", "split")
    _, left, right = scripts.values()
    assert left.id == f"{chunk.id}-p01" and right.id == f"{chunk.id}-p02"
    assert left.text + right.text == chunk.text and "12.5 cm" in left.text
    assert result.accepted_candidate_ids == (("a000003", "a000004") if split_passes else ())
    assert result.status == ("accepted" if split_passes else "exhausted")


def test_equation_never_splits_and_unavailable_asr_does_not_resynthesize():
    chunk = script_chunk("Take a plus b, then divide by c.", policy="equation_arithmetic")
    first = candidate(chunk, "a000001")
    result = verify_chunk(chunk, first, "en", lambda current, *_: candidate(current, "a000002"),
                          lambda *_: Transcription(""), 0.15)
    assert result.status == "exhausted" and len(result.attempts) == 2
    assert "no safe split" in result.reason
    unavailable = verify_chunk(chunk, first, "en", unexpected,
                               lambda *_: Transcription(None, ("a000003",), "Key required."), 0.15)
    assert unavailable.status == "unavailable" and unavailable.reason == "Key required."
    assert unavailable.attempts[0].comparison.cer is None
    assert compare_speech(" ॥ ", "some words").cer is None


def test_cuda_oom_falls_back_with_same_audio_language_and_candidate_id(tmp_path):
    source = script_chunk("A triangle has three sides.")
    clip = candidate(source, "a000042")
    wav = wav_bytes()
    audio_path = tmp_path / clip.audio_path
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(wav)
    log = AttemptLog()
    local_calls = []
    releases = []

    def local(audio, language):
        local_calls.append((audio, language))
        raise RuntimeError("CUDA out of memory")

    def hosted(request):
        assert request.url == httpx.URL("https://api.bodhan.ai/v1/audio/transcriptions")
        assert request.headers["Authorization"] == "Bearer asr-test-key"
        assert request.headers["Content-Type"].startswith("multipart/form-data;")
        assert b'name="model"\r\n\r\nindic-transcribe' in request.content
        assert b'name="language"\r\n\r\nen' in request.content
        assert b'name="file"; filename="a000042.wav"' in request.content
        assert wav in request.content
        return httpx.Response(200, json={"text": source.text})

    with httpx.Client(transport=httpx.MockTransport(hosted)) as client:
        provider = IndicTranscribeProvider("local-first", Inference(log), client,
                                           {"BODHAN_TRANSCRIBE_KEY": "asr-test-key"},
                                           local, lambda: releases.append(True))
        result = provider.transcribe(clip, "en", tmp_path)

    assert local_calls == [(wav, "en")] and releases == [True]
    assert result.text == source.text
    local_attempt, hosted_attempt = log.attempts
    assert result.attempt_ids == (local_attempt.id, hosted_attempt.id)
    assert local_attempt.entity_ids == hosted_attempt.entity_ids == (source.id, "en", "a000042")
    assert local_attempt.backend == "local" and local_attempt.error_category == "cuda_oom"
    assert hosted_attempt.backend == "hosted" and hosted_attempt.fallback_reason == CUDA_OOM
    assert hosted_attempt.usage.quantity == pytest.approx(2.5 / 60)


def test_hosted_duration_limit_is_explicit_and_not_billed(tmp_path):
    clip = replace(candidate(script_chunk("Take your time."), "a000001"), duration_seconds=31)
    audio_path = tmp_path / clip.audio_path
    audio_path.parent.mkdir(parents=True)
    audio_path.write_bytes(wav_bytes())
    log = AttemptLog()
    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        provider = IndicTranscribeProvider("hosted", Inference(log), client,
                                           {"BODHAN_TRANSCRIBE_KEY": "asr-test-key"}, unexpected, unexpected)
        result = provider.transcribe(clip, "en", tmp_path)
    assert result.error_category == "duration_limit" and "30-second" in result.reason
    assert log.attempts[0].usage.cost_inr == 0


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\0\0" * 6000)
    return buffer.getvalue()


@pytest.mark.parametrize("language", ["hi", "ta"])
def test_speech_qa_checkpoint_reuses_existing_synthesis(tmp_path, monkeypatch, language):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BODHAN_BACKEND", "local-first")
    for name in ("BODHAN_TRANSLATE_GGUF_URL", "BODHAN_SPEECH_MAX_CHARS", "BODHAN_SPEECH_CER_THRESHOLD",
                 "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    scans = tmp_path / "scans"
    scans.mkdir()
    with Image.new("RGB", (200, 300), "white") as image:
        image.save(scans / "one.png")

    text = "ज्यामिति।" if language == "hi" else "வடிவியல்."
    calls = []

    def recognize(self, png, options):
        calls.append("ocr")
        return {"markdown": "# Geometry\n\nAn angle has two rays."}

    def translate(self, input_text, language):
        calls.append("translate")
        return ModelTranslation(text)

    def speak(self, input_text, voice):
        assert input_text == text
        calls.append("speech")
        return wav_bytes()

    def transcribe(self, audio, target):
        assert audio == wav_bytes() and target == language
        calls.append("asr")
        return text

    monkeypatch.setattr(runner.LocalOCR, "__call__", recognize)
    monkeypatch.setattr(runner.LocalTranslate, "__call__", translate)
    monkeypatch.setattr(runner.LocalSpeak, "__call__", speak)
    monkeypatch.setattr(runner.LocalTranscribe, "__call__", transcribe)
    out = tmp_path / "out"
    args = ["--input", str(scans), "--out", str(out), "--langs", language, "--source-lang", "en", "--until"]
    assert main([*args, "speech"]) == 0
    candidate_artifact = json.loads((out / language / "chunks.json").read_text(encoding="utf-8"))
    calls.clear()

    if language == "ta":
        monkeypatch.setattr(runner, "SPEECH_QA_POLICY_VERSION", 2)
    assert main([*args, "speech-qa"]) == 0
    assert calls == ["asr"] * len(candidate_artifact["chunks"])
    report_path = out / language / "speech-qa.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["metrics"]["cer"] == 0 and report["metrics"]["coverage"] == 1
    assert report["attempts"][0]["backend"] == "local"
    reviews = [item for section in report["sections"] for item in section["chunks"]]
    for review, clip in zip(reviews, candidate_artifact["chunks"], strict=True):
        assert review["accepted_candidate_ids"] == [clip["attempt_id"]]
        assert review["attempts"][0]["comparison"]["reference"] == text

    calls.clear()
    if language == "ta":
        monkeypatch.setattr(runner, "SPEECH_QA_POLICY_VERSION", 3)
    assert main([*args, "speech-qa"]) == 0
    assert calls == []
    assert json.loads(report_path.read_text(encoding="utf-8")) == report
