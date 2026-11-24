import pytest
from narrator import config
from narrator.errors import BodhanAPIError, InputError, TruncatedTranslationError
from narrator.pipeline import check_input, narrate, plan_chunks

from tests.helpers import FakeClient, duration

TEXT = "One. Two.\n\nThree."  # 2 paragraphs, 3 sentences


def run(client=None, text=TEXT, **kwargs):
    client = client or FakeClient()
    return client, narrate(text, "hi", "Kavya", None, client, **kwargs)


def test_full_run_makes_one_translation_per_paragraph():
    client, result = run()
    assert client.translate_calls == ["One. Two.", "Three."]
    assert result.paragraphs == ["One. Two.", "Three."]
    assert len(result.chunks) == 3


def test_speech_gets_voice_language_and_style():
    client = FakeClient()
    narrate(TEXT, "hi", "Amit", "news", client)
    assert all(call[1:] == ("Amit", "hi", "news") for call in client.speak_calls)


def test_audio_has_sentence_and_paragraph_pauses():
    # 3 clips of 0.2 s + 0.3 s (sentence gap) + 0.7 s (paragraph gap) = 1.6 s
    _, result = run()
    assert round(duration(result.wav_bytes), 2) == 1.6


def test_truncated_translation_stops_before_any_speech():
    client = FakeClient(truncated_paragraphs={2})
    with pytest.raises(TruncatedTranslationError) as error:
        narrate(TEXT, "hi", "Kavya", None, client)
    assert error.value.paragraph_number == 2
    assert client.speak_calls == []


def test_translation_is_kept_when_speech_fails():
    kept = []
    client = FakeClient(fail_on_speak=True)
    with pytest.raises(BodhanAPIError):
        narrate(TEXT, "hi", "Kavya", None, client, on_translated=kept.extend)
    assert kept == ["[hi] One. Two.", "[hi] Three."]


def test_progress_runs_in_order():
    events = []
    run(progress=lambda stage, done, total: events.append((stage, done, total)))
    stages = [e[0] for e in events]
    assert stages == sorted(stages, key=lambda s: s == "speak")  # translate first
    assert events[0] == ("translate", 0, 2)
    assert events[-1] == ("speak", 3, 3)


def test_long_sentence_is_cut_into_speakable_chunks():
    long_sentence = ", ".join(f"word number {i}" for i in range(80)) + "."
    client, _ = run(text=long_sentence)
    assert len(client.speak_calls) > 1
    assert all(len(call[0]) <= config.CHUNK_LIMIT for call in client.speak_calls)


def test_plan_chunks_pauses():
    chunks, pauses = plan_chunks(["A. B.", "C."])
    assert chunks == ["A.", "B.", "C."]
    assert pauses == [config.SENTENCE_PAUSE_MS, config.PARAGRAPH_PAUSE_MS]


@pytest.mark.parametrize("text", ["", "   \n  "])
def test_empty_text_is_rejected(text):
    with pytest.raises(InputError):
        check_input(text, "hi", "Kavya")


def test_text_over_the_limit_is_rejected():
    with pytest.raises(InputError):
        check_input("a" * (config.MAX_INPUT_CHARS + 1), "hi", "Kavya")


def test_text_exactly_at_the_limit_is_accepted():
    check_input("a" * config.MAX_INPUT_CHARS, "hi", "Kavya")


def test_unknown_language_and_wrong_voice_are_rejected():
    with pytest.raises(InputError):
        check_input("Hello.", "xx", "Kavya")
    with pytest.raises(InputError):
        check_input("Hello.", "ta", "Kavya")


def test_bad_input_makes_no_api_calls():
    client = FakeClient()
    with pytest.raises(InputError):
        narrate("", "hi", "Kavya", None, client)
    assert client.translate_calls == []