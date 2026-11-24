import json

import httpx
import pytest

from bodhan_lesson import config
from bodhan_lesson.audio import wav
from bodhan_lesson.clients.bodhan import BodhanClient, BodhanError, TruncatedTranslation
from bodhan_lesson.stages import localize
from bodhan_lesson.text import chunking

KEYS = config.BodhanKeys("t-key", "s-key")


def tone(seconds: float = 1.0, rate: int = 24000) -> bytes:
    return wav.join([b"\x01\x01" * round(seconds * rate)], rate)


def client_for(handler, tmp_path=None) -> BodhanClient:
    return BodhanClient(KEYS, cache_dir=tmp_path, http=httpx.Client(transport=httpx.MockTransport(handler)),
                        sleep=lambda s: None)


def translation(text: str, finish: str = "stop") -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": text}, "finish_reason": finish}]})


# ---- chunking ----------------------------------------------------------------------------
def test_chunks_pack_whole_sentences_up_to_the_limit():
    text = "One two three. Four five six. Seven eight nine."
    assert chunking.chunk_text(text, 30) == ["One two three. Four five six.", "Seven eight nine."]
    assert chunking.chunk_text(text, 1000) == [text]


def test_chunks_split_on_devanagari_danda():
    assert chunking.chunk_text("पहला वाक्य। दूसरा वाक्य।", 14) == ["पहला वाक्य।", "दूसरा वाक्य।"]


def test_an_over_long_sentence_is_cut_at_a_comma_or_space():
    chunks = chunking.chunk_text("alpha beta gamma, delta epsilon zeta eta theta", 20)
    assert all(len(c) <= 20 for c in chunks) and " ".join(chunks).replace("  ", " ") == "alpha beta gamma, delta epsilon zeta eta theta"


# ---- wav -------------------------------------------------------------------------------------
def test_wav_join_and_duration_round_trip():
    frames, rate = wav.read_wav(tone(0.5))
    joined = wav.join([frames, wav.silence(0.25, rate), frames], rate)
    assert wav.duration_s(joined) == pytest.approx(1.25)


def test_read_wav_rejects_garbage():
    with pytest.raises(wav.WavError):
        wav.read_wav(b"not a wav")


# ---- client -------------------------------------------------------------------------------------
def test_translate_sends_target_language_and_caches(tmp_path):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return translation("नमस्ते")

    client = client_for(handler, tmp_path)
    assert client.translate("Hello", "hi") == "नमस्ते"
    assert client.translate("Hello", "hi") == "नमस्ते"
    assert len(sent) == 1 and sent[0]["target_language"] == "hi" and sent[0]["model"] == "indic-translate"
    assert "max_tokens" not in sent[0] and "max_output_tokens" in sent[0]


def test_truncated_translation_is_reported():
    with pytest.raises(TruncatedTranslation):
        client_for(lambda r: translation("half a sent", finish="length")).translate("Hello", "hi")


def test_speak_returns_wav_and_retries_on_429():
    replies = iter([httpx.Response(429, text="slow down"), httpx.Response(200, content=tone())])
    audio = client_for(lambda r: next(replies)).speak("Hello", "Kavya", "en")
    assert wav.duration_s(audio) == pytest.approx(1.0)


def test_client_errors_are_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, text="bad key")

    with pytest.raises(BodhanError):
        client_for(handler).speak("Hello", "Kavya", "en")
    assert len(calls) == 1


# ---- stage ---------------------------------------------------------------------------------------
def test_translate_unit_falls_back_to_sentences_when_truncated():
    def handler(request):
        text = json.loads(request.content)["messages"][0]["content"]
        return translation("cut", "length") if ". " in text else translation(f"[{text}]")

    text, warnings = localize.translate_unit(client_for(handler), "First one. Second one.", "hi")
    assert text == "[First one.] [Second one.]" and warnings


def test_english_is_never_translated():
    text, warnings = localize.translate_unit(client_for(lambda r: pytest.fail("no call expected")), "Hello.", "en")
    assert (text, warnings) == ("Hello.", [])


def test_speak_chunk_uses_the_alternate_voice_after_a_failure():
    def handler(request):
        voice = json.loads(request.content)["voice"]
        return httpx.Response(400, text="no voice") if voice == "Kavya" else httpx.Response(200, content=tone(2.0))

    result = localize.speak_chunk(client_for(handler), "x" * 40, "hi")
    assert result["voice"] == "Amit" and result["status"] == "ok" and len(result["attempts"]) == 2


def test_speak_chunk_reports_failure_without_raising():
    result = localize.speak_chunk(client_for(lambda r: httpx.Response(400, text="no")), "Hello there.", "hi")
    assert result["status"] == "failed" and result["audio"] is None


def test_localize_language_writes_script_json(tmp_path):
    script = {"sections": [{"section_id": "s01", "title": "Intro", "slug": "intro",
                            "units": [{"id": "s01.u01", "kind": "explanation", "text": "Hello class."}]}]}

    def handler(request):
        if request.url.path.endswith("/chat/completions"):
            return translation("नमस्ते कक्षा।")
        return httpx.Response(200, content=tone(3.0))

    report = localize.localize_language(script, "hi", client_for(handler), tmp_path)
    saved = json.loads((tmp_path / "hi" / "narration_script.hi.json").read_text(encoding="utf-8"))
    unit = saved["sections"][0]["units"][0]
    assert unit["text"] == "नमस्ते कक्षा।" and unit["chunks"][0]["text"] == "नमस्ते कक्षा।"
    assert saved["ai_generated"] and report["chunks"] == 1 and report["chunks_failed"] == 0
    assert (tmp_path / "hi" / "lesson.hi.mp3").exists() or (tmp_path / "hi" / "lesson.hi.wav").exists()


def test_an_exhausted_budget_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, text='{"error":{"type":"budget_exceeded"}}')

    with pytest.raises(BodhanError, match="budget"):
        client_for(handler).translate("Hello", "hi")
    assert len(calls) == 1


# ---- back-transcription ---------------------------------------------------------------------------
def test_cer_ignores_punctuation_and_counts_character_edits():
    from bodhan_lesson.text import cer
    assert cer.cer("Hello, world.", "hello world") == 0.0
    assert cer.cer("abcd", "abxd") == pytest.approx(0.25)
    assert cer.cer("", "anything") == 0.0


def test_transcribe_posts_multipart_and_caches(tmp_path):
    sent = []

    def handler(request):
        sent.append(request.headers["content-type"])
        return httpx.Response(200, json={"text": "नमस्ते"})

    keys = config.BodhanKeys("t", "s", "x")
    client = BodhanClient(keys, cache_dir=tmp_path, http=httpx.Client(transport=httpx.MockTransport(handler)),
                          sleep=lambda s: None)
    assert client.transcribe(tone(), "hi") == "नमस्ते" and client.transcribe(tone(), "hi") == "नमस्ते"
    assert len(sent) == 1 and sent[0].startswith("multipart/form-data")


def test_speak_chunk_retries_with_the_other_voice_when_the_transcript_is_far_off():
    def handler(request):
        if request.url.path.endswith("/audio/speech"):
            return httpx.Response(200, content=tone(2.0))
        return httpx.Response(200, json={"text": "zzzz"})

    keys = config.BodhanKeys("t", "s", "x")
    client = BodhanClient(keys, http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    result = localize.speak_chunk(client, "x" * 40, "hi")
    assert result["status"] == "high_cer" and len(result["attempts"]) == 2 and result["cer"] > 0.45
