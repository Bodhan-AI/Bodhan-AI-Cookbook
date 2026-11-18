import wave

from bodhan_agent.bodhan import MAX_TRANSLATE_CHARS, MAX_TTS_CHARS, _split_text, _translation_chunks
from bodhan_agent.bodhan import _duration_seconds


def test_translation_chunks_stay_under_limit_and_preserve_input():
    text = ("Paragraph one.\n\n" + "x" * (MAX_TRANSLATE_CHARS + 100) + "\n\nParagraph three.")
    chunks = _translation_chunks(text)
    assert len(chunks) > 1
    assert all(len(chunk) <= MAX_TRANSLATE_CHARS for chunk in chunks)
    assert "".join(chunks) == text


def test_short_translation_is_not_changed():
    text = "## Heading\n\nA sentence with a table."
    assert _translation_chunks(text) == [text]


def test_tts_chunks_at_sentence_or_word_boundaries():
    text = ("A short sentence. " * 100).strip()
    chunks = _split_text(text)
    assert len(chunks) > 1
    assert all(len(chunk) <= MAX_TTS_CHARS for chunk in chunks)
    assert " ".join(chunks) == text


def test_wav_duration_uses_standard_library_without_ffprobe(tmp_path, monkeypatch):
    wav_path = tmp_path / "one_second.wav"
    with wave.open(str(wav_path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\0" * 24000)

    def no_external_probe(_command):
        raise AssertionError("WAV duration should not need ffprobe")

    monkeypatch.setattr("bodhan_agent.bodhan.shutil_which", no_external_probe)
    assert _duration_seconds(wav_path) == 1.0
