"""Each tool against the fake Bodhan API: limits, verification, errors, caching, policy."""

import json
from pathlib import Path

import pytest

from bodhan_mcp.errors import ToolError


def test_transcribe_long_audio_chunks_and_detects_language(kit, fake, media_dir):
    r = kit.transcribe_audio(str(media_dir / "voice_note.wav"))
    assert r["ok"] and r["segments"] >= 3
    assert r["language"] == "hi" and r["language_detection"]["script"] == "Devanagari"
    langs = [c[1]["language"] for c in fake.calls]
    assert langs[0] is None and set(langs[1:]) == {"hi"}  # detected once, then used as a hint
    data = json.loads(Path(r["transcript_json"]).read_text())
    assert data["segments"][0]["start"] == 0 and data["segments"][-1]["end"] > 60


def test_silence_returns_warning_not_text(kit, fake, media_dir):
    r = kit.transcribe_audio(str(media_dir / "silence.wav"))
    assert r["text"] == "" and "No speech" in r["warning"] and fake.count("/v1/audio/transcriptions") == 0


def test_same_input_never_sent_twice(kit, fake, media_dir):
    kit.transcribe_audio(str(media_dir / "clip_te.wav"))
    n = len(fake.calls)
    kit.transcribe_audio(str(media_dir / "clip_te.wav"))
    assert len(fake.calls) == n
    assert kit.client.ledger.records[-1].cached


def test_translate_preserves_table_and_numbers(kit, media_dir):
    src = media_dir / "lesson.md"
    src.write_text("# Angles\n\nA right angle is 90 degrees.\n\n| name | degrees |\n|---|---|\n| right | 90 |\n| straight | 180 |\n")
    r = kit.translate_text("mr", text_path=str(src))
    assert r["checks"]["passed"], r["checks"]
    out = Path(r["path"]).read_text()
    assert "| 90 |" in out and out.count("|") == src.read_text().count("|")


def test_translation_check_catches_missing_numbers():
    from bodhan_mcp.tools import translation_checks

    c = translation_checks("Take 2 tablets at 9 pm", "गोली रात को लें", "hi", "native", [])
    assert not c["passed"] and any("numbers missing" in p for p in c["problems"])


def test_unsupported_language_is_a_clear_error(kit):
    with pytest.raises(ToolError) as e:
        kit.translate_text("fr", text="hello")
    assert e.value.code == "unsupported_language" and "invent" in e.value.hint


def test_tts_verifies_and_regenerates_bad_sentence(kit, fake, media_dir):
    tr = kit.translate_text("hi", text_path=str(media_dir / "paragraph.txt"))
    hindi = Path(tr["path"]).read_text().strip()
    first_sentence = hindi.split(".")[0] + "."
    fake.corrupt_tts_once.add(first_sentence)
    r = kit.synthesize_speech("hi", text_path=tr["path"], gender="female")
    v = r["verification"]
    assert r["path"].endswith(".mp3") and r["voice"] == "Kavya"
    assert v["regenerated"] == 1 and v["passed"] and v["max_cer"] == 0
    assert r["label"] == "AI-generated speech"


def test_llm_cannot_inject_its_own_indic_text(kit):
    with pytest.raises(ToolError) as e:
        kit.synthesize_speech("hi", text="यह मैंने खुद लिखा है")
    assert e.value.code == "policy_violation"
    with pytest.raises(ToolError):
        kit.write_text("x.md", "यह हिंदी है")


def test_tts_rejects_english_text_for_indic_voice(kit):
    with pytest.raises(ToolError) as e:
        kit.synthesize_speech("bn", text="Hello there")
    assert "Translate it first" in e.value.hint


def test_ocr_multi_page_pdf(kit, fake, media_dir):
    r = kit.ocr_document(str(media_dir / "chapter.pdf"))
    assert r["pages"] == 3 and fake.count("/v1/chat/completions") == 3
    assert r["low_confidence_blocks"] == 3 and "untrusted" in r["note"].lower()
    r2 = kit.ocr_document(str(media_dir / "chapter.pdf"), pages="2")
    assert r2["pages"] == 1


def test_subtitles_translated_srt(kit, media_dir):
    t = kit.transcribe_audio(str(media_dir / "voice_note.wav"), language="hi")
    s = kit.create_subtitles(t["transcript_json"], target_language="en")
    body = Path(s["path"]).read_text()
    assert s["path"].endswith(".en.srt") and body.startswith("1\n00:00:00,000 --> ")
    assert s["cues"] >= t["segments"]


def test_missing_file_and_bad_args_are_structured(kit):
    with pytest.raises(ToolError) as e:
        kit.ocr_document("/nope/missing.pdf")
    assert e.value.to_dict()["error"]["code"] == "file_not_found"
    with pytest.raises(ToolError) as e:
        kit.translate_text("hi")
    assert e.value.code == "invalid_input"
    with pytest.raises(ToolError):
        kit.ocr_document("/dev/null")


def test_retries_on_429_then_succeeds(kit, fake):
    fake.fail_next = [429, 503]
    r = kit.translate_text("ta", text="Good morning")
    assert r["ok"]


def test_4xx_is_not_retried(kit, fake):
    fake.fail_next = [400, 400, 400]
    with pytest.raises(ToolError) as e:
        kit.translate_text("ta", text="Good morning")
    assert "not retryable" in e.value.hint and fake.fail_next == [400, 400]


def test_translate_falls_back_to_chat_transport_on_404(kit, fake):
    fake.translate_route_404 = True
    r = kit.client.translate("Class starts at 9.", "hi")
    assert r["translation"] and r["truncated"] is False
    assert [p for p, _ in fake.calls][-2:] == ["/translate", "/v1/chat/completions"]
    kit.client.translate("Second sentence.", "hi")
    assert fake.calls[-1][0] == "/v1/chat/completions"  # the 404 is remembered; no wasted request
