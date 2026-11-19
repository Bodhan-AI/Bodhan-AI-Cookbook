"""Tests for pipeline.py. They need no API key and make no network calls.

    pytest test_pipeline.py
"""

import io
import json
import wave

import pipeline
import pytest
from PIL import Image


def make_wav(n_frames: int) -> bytes:
    """A silent WAV in the format indic-speak returns (PCM16, 24 kHz, mono)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * n_frames)
    return buf.getvalue()


class FakeResponse:
    def __init__(self, payload=None, content=b""):
        self._payload, self.content = payload, content
        self.ok, self.status_code = True, 200

    def json(self):
        return self._payload


# --- text cleaning -------------------------------------------------------------


def test_split_units_strips_markdown():
    md = "# Class 8 Science\n\n**Time:** 30 minutes\n- item one\n| a | b |"
    assert pipeline.split_units(md) == ["Class 8 Science", "Time: 30 minutes", "item one", "a b"]


def test_split_units_joins_wrapped_lines_into_one_paragraph():
    # Found with a real textbook photo: each printed line was translated on its own,
    # which turned one sentence into several broken fragments.
    text = "सारे गाँव में बदलू मुझे सबसे\nअच्छा आदमी लगता था क्योंकि\nवह मुझे गोलियाँ देता था।"
    assert pipeline.split_units(text) == [
        "सारे गाँव में बदलू मुझे सबसे अच्छा आदमी लगता था क्योंकि वह मुझे गोलियाँ देता था।"
    ]


def test_split_units_keeps_numbered_questions_apart():
    text = "1. Define force and\ngive its unit.\n2. What is inertia?\n(a) mass (b) speed"
    assert pipeline.split_units(text) == [
        "1. Define force and give its unit.",
        "2. What is inertia?",
        "(a) mass (b) speed",
    ]


def test_split_units_keeps_fields_on_one_printed_line_apart():
    # Found in live testing: joined into one phrase, these two fields were mistranslated.
    text = "Time: 30 minutes      Maximum marks: 10"
    assert pipeline.split_units(text) == ["Time: 30 minutes", "Maximum marks: 10"]


def ocr_response(*blocks):
    """A fake OCR response with (label, text) layout blocks."""
    return {
        "choices": [{"message": {"content": "\n\n".join(text for _, text in blocks)}}],
        "blocks": [{"order": i, "label": label, "text": text} for i, (label, text) in enumerate(blocks)],
    }


def test_paper_units_marks_titles_and_skips_pictures():
    ocr = ocr_response(
        ("Chapter-title", "लाख की चूड़ियाँ"),
        ("Paragraph", "सारे गाँव में बदलू\nमुझे अच्छा लगता था।"),
        ("Image", ""),
    )
    assert pipeline.paper_units(ocr) == [
        ("title", "लाख की चूड़ियाँ"),
        ("text", "सारे गाँव में बदलू मुझे अच्छा लगता था।"),
    ]


def test_paper_units_drops_overlapping_ocr_blocks():
    # Found in live testing: two overlapping layout blocks returned the same text twice.
    ocr = ocr_response(
        ("Infobox", "Time: 30 minutes"),
        ("Folio", "Time: 30 minutes      Maximum marks: 10"),
    )
    assert pipeline.paper_units(ocr) == [("text", "Time: 30 minutes"), ("text", "Maximum marks: 10")]


def test_paper_units_falls_back_to_the_markdown_without_blocks():
    ocr = {"choices": [{"message": {"content": "# Heading\n\n1. A question"}}]}
    assert pipeline.paper_units(ocr) == [("text", "Heading"), ("text", "1. A question")]


def test_remove_repeats_keeps_the_longer_neighbour():
    short, long = ("text", "Time"), ("text", "Time: 30 minutes")
    assert pipeline.remove_repeats([short, long]) == [long]
    assert pipeline.remove_repeats([long, short]) == [long]


def test_remove_repeats_leaves_non_neighbours_alone():
    units = [("title", "Section A"), ("text", "1. Define force."), ("title", "Section A")]
    assert pipeline.remove_repeats(units) == units


# --- same-language check -------------------------------------------------------


def test_already_in_recognises_the_script():
    assert pipeline.already_in("लाख की चूड़ियाँ", "hi")
    assert pipeline.already_in("What is photosynthesis?", "en")
    assert not pipeline.already_in("What is photosynthesis?", "hi")
    assert not pipeline.already_in("लाख की चूड़ियाँ", "ta")
    assert not pipeline.already_in("120 + 30 = ?", "hi")  # no letters: let the API decide


def test_translate_keeps_text_already_in_the_target_language(monkeypatch):
    # Found with a real photo: "translating" Hindi into Hindi reworded it and added mistakes.
    def fail(path, body):
        raise AssertionError("the API must not be called")

    monkeypatch.setattr(pipeline, "bodhan", fail)
    assert pipeline.translate("सारे गाँव में बदलू", "hi") == "सारे गाँव में बदलू"


# --- speech chunking -----------------------------------------------------------


def test_chunk_for_speech_respects_the_limit_and_keeps_all_text():
    text = "पहला वाक्य। दूसरा वाक्य? Third sentence. " * 15
    chunks = pipeline.chunk_for_speech(text, limit=100)
    assert len(chunks) > 1
    assert all(len(c) <= 100 for c in chunks)
    assert " ".join(chunks).split() == text.split()


def test_chunk_for_speech_splits_on_the_hindi_full_stop():
    assert pipeline.chunk_for_speech("पहला वाक्य। दूसरा वाक्य।", limit=15) == ["पहला वाक्य।", "दूसरा वाक्य।"]


# --- keys ----------------------------------------------------------------------


def test_api_key_prefers_the_per_model_key(monkeypatch):
    monkeypatch.setenv("BODHAN_API_KEY", "shared")
    monkeypatch.setenv("BODHAN_OCR_KEY", "ocr-only")
    monkeypatch.delenv("BODHAN_SPEAK_KEY", raising=False)
    assert pipeline.api_key("indic-ocr") == "ocr-only"
    assert pipeline.api_key("indic-speak") == "shared"


def test_api_key_missing_gives_a_clear_error(monkeypatch):
    for var in ["BODHAN_API_KEY", *pipeline.KEY_VARS.values()]:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError, match="BODHAN_TRANSLATE_KEY"):
        pipeline.api_key("indic-translate")


# --- API calls against fake responses -----------------------------------------


def test_read_paper_calls_the_api_once_per_image(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "CACHE_DIR", tmp_path)
    calls = []

    def fake_bodhan(path, body):
        calls.append(body["model"])
        return FakeResponse({"choices": [{"message": {"content": "1. Q"}}], "blocks": []})

    monkeypatch.setattr(pipeline, "bodhan", fake_bodhan)
    first = pipeline.read_paper(b"same image bytes")
    second = pipeline.read_paper(b"same image bytes")
    assert calls == ["indic-ocr"]  # the second read came from the cache
    assert first == second


def test_translate_sends_the_target_language(monkeypatch):
    sent = {}

    def fake_bodhan(path, body):
        sent.update(path=path, body=body)
        return FakeResponse({"choices": [{"message": {"content": "नमस्ते"}, "finish_reason": "stop"}]})

    monkeypatch.setattr(pipeline, "bodhan", fake_bodhan)
    assert pipeline.translate("Hello", "hi") == "नमस्ते"
    assert sent["path"] == "/v1/chat/completions"
    assert sent["body"]["model"] == "indic-translate"
    assert sent["body"]["target_language"] == "hi"


def test_speak_joins_chunks_into_one_wav(monkeypatch):
    bodies = []

    def fake_bodhan(path, body):
        bodies.append(body)
        return FakeResponse(content=make_wav(2400))  # 0.1 s each

    monkeypatch.setattr(pipeline, "bodhan", fake_bodhan)
    text = "पहला वाक्य। " * 40  # long enough to need several requests
    audio = pipeline.speak(text, "hi")

    assert len(bodies) > 1
    assert all(json.loads(b["instructions"])["lang"] == "hi" for b in bodies)
    with wave.open(io.BytesIO(audio)) as w:
        assert w.getnframes() == 2400 * len(bodies)
        assert w.getframerate() == 24000


# --- photo preparation ---------------------------------------------------------


def make_image(size, fmt, orientation=None) -> bytes:
    img = Image.new("RGB", size, "white")
    buf = io.BytesIO()
    if orientation:
        exif = img.getexif()
        exif[0x0112] = orientation
        img.save(buf, fmt, exif=exif)
    else:
        img.save(buf, fmt)
    return buf.getvalue()


def test_prepare_image_leaves_a_small_upright_image_untouched():
    raw = make_image((800, 600), "PNG")
    assert pipeline.prepare_image(raw) == (raw, "image/png")


def test_prepare_image_turns_and_shrinks_a_phone_photo():
    raw = make_image((4000, 3000), "JPEG", orientation=6)  # 6 = camera held upright
    prepared, mime = pipeline.prepare_image(raw)
    with Image.open(io.BytesIO(prepared)) as img:
        assert img.size == (1500, 2000)  # now portrait, longest side capped at 2000
    assert mime == "image/jpeg"


# --- rate limits ---------------------------------------------------------------


def test_bodhan_waits_and_retries_on_429(monkeypatch):
    monkeypatch.setenv("BODHAN_API_KEY", "test-key")
    waits, replies = [], [429, 429, 200]

    class Reply:
        def __init__(self, status):
            self.status_code, self.ok, self.headers = status, status == 200, {}

    monkeypatch.setattr(pipeline.requests, "post", lambda *a, **k: Reply(replies.pop(0)))
    monkeypatch.setattr(pipeline.time, "sleep", waits.append)
    resp = pipeline.bodhan("/v1/audio/speech", {"model": "indic-speak"})
    assert resp.status_code == 200
    assert waits == [5, 10]


# --- voice answers -------------------------------------------------------------


def test_is_question_finds_numbered_questions_only():
    assert pipeline.is_question("1. Define force.")
    assert pipeline.is_question("Q3 What is mass")
    assert pipeline.is_question("प्रश्न 2 न्यूटन का नियम लिखिए")
    assert pipeline.is_question("Why is the sky blue?")
    assert not pipeline.is_question("Time: 30 minutes")
    assert not pipeline.is_question("Class 8 Science")


def test_split_wav_cuts_long_recordings_and_keeps_every_frame():
    audio = make_wav(24000 * 60)  # one minute
    pieces = pipeline.split_wav(audio, seconds=25)
    lengths = []
    for piece in pieces:
        with wave.open(io.BytesIO(piece)) as w:
            lengths.append(w.getnframes())
    assert lengths == [24000 * 25, 24000 * 25, 24000 * 10]


def test_transcribe_sends_each_piece_as_a_file_and_joins_the_text(monkeypatch):
    sent = []

    def fake_bodhan(path, body, files=None):
        sent.append((path, body, files))
        return FakeResponse({"text": f" part {len(sent)} "})

    monkeypatch.setattr(pipeline, "bodhan", fake_bodhan)
    text = pipeline.transcribe(make_wav(24000 * 40), "hi")  # 40 s: two pieces

    assert text == "part 1 part 2"
    assert [path for path, _, _ in sent] == ["/v1/audio/transcriptions"] * 2
    assert all(body == {"model": "indic-transcribe", "language": "hi"} for _, body, _ in sent)
    name, data, mime = sent[0][2]["file"]
    assert (name, mime) == ("answer_0.wav", "audio/wav")
    assert data.startswith(b"RIFF")  # a complete WAV file, not raw frames


def test_bodhan_sends_a_form_when_there_is_a_file(monkeypatch):
    monkeypatch.setenv("BODHAN_API_KEY", "test-key")
    calls = []

    class Reply:
        status_code, ok, headers = 200, True, {}

    monkeypatch.setattr(pipeline.requests, "post", lambda *a, **k: calls.append(k) or Reply())
    pipeline.bodhan("/v1/audio/transcriptions", {"model": "indic-transcribe"}, files={"file": b"x"})
    pipeline.bodhan("/v1/audio/speech", {"model": "indic-speak"})

    assert calls[0]["data"] == {"model": "indic-transcribe"} and "json" not in calls[0]
    assert calls[1]["json"] == {"model": "indic-speak"} and "files" not in calls[1]


def test_is_question_ignores_a_decimal_number():
    assert not pipeline.is_question("3.5 kg of sugar is dissolved in water")
    assert pipeline.is_question("3. 5 kg of sugar is dissolved in water. Find the mass.")


def test_transcribe_refuses_bad_or_overlong_audio_before_any_paid_call(monkeypatch):
    def no_calls(*args, **kwargs):
        raise AssertionError("the API must not be called")

    monkeypatch.setattr(pipeline, "bodhan", no_calls)
    with pytest.raises(RuntimeError, match="could not be read"):
        pipeline.transcribe(b"this is not audio", "hi")
    with pytest.raises(RuntimeError, match="longer than 3 minutes"):
        pipeline.transcribe(make_wav(24000 * (pipeline.MAX_ANSWER_SECONDS + 1)), "hi")


def test_transcribe_returns_empty_text_for_silence(monkeypatch):
    monkeypatch.setattr(pipeline, "bodhan", lambda *a, **k: FakeResponse({"text": None}))
    assert pipeline.transcribe(make_wav(24000), "hi") == ""
