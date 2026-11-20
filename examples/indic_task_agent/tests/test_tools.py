import json
from pathlib import Path

import httpx
import pytest
from bodhan_mcp.tools import Context, make_tools
from conftest import make_client, needs_ffmpeg, wav_bytes
from PIL import Image

TABLE_MD = "# Worksheet\n\n| Item | Qty |\n|---|---|\n| Pen | 4 |\n\nAdd 25 and 3.\n"


def tool(ctx, name):
    return next(f for f in make_tools(ctx) if f.__name__ == name)


def fake_bodhan(state, *, drop_digits=False, transcript=None):
    def handler(req):
        path = req.url.path
        if path == "/translate":
            body = json.loads(req.content)
            out = body["text"] if not drop_digits else "".join(ch for ch in body["text"] if not ch.isdigit())
            state.setdefault("translate_calls", []).append(body)
            return httpx.Response(200, json={"translation": f"[{body['target_language']}] {out}", "target_language": body["target_language"],
                                             "script": "native", "truncated": False, "usage": {"completion_tokens": len(out)}})
        if path == "/v1/audio/speech":
            body = json.loads(req.content)
            state["last_speech"] = body["input"]
            return httpx.Response(200, content=wav_bytes(max(0.5, len(body["input"]) * 0.05)), headers={"content-type": "audio/wav"})
        if path == "/v1/audio/transcriptions":
            text = transcript(state) if transcript else state.get("last_speech", "")
            return httpx.Response(200, json={"text": text})
        if path == "/v1/chat/completions":
            choice = {"message": {"role": "assistant", "content": "# Page text"}, "finish_reason": state.get("finish", "stop"), "index": 0}
            return httpx.Response(200, json={"choices": [choice],
                                             "blocks": [{"order": 0, "label": "Title", "type": "Title", "bbox_xyxy": [0, 0, 1, 1], "conf": 0.2, "text": "x"}]})
        return httpx.Response(404, json={"error": {"code": "not_found", "message": path}})

    return handler


@pytest.fixture
def make_ctx(tmp_path):
    def build(handler):
        client = make_client(tmp_path, handler)
        return Context(client=client, work_dir=tmp_path / "work")

    return build


def test_translate_keeps_table_and_saves_file(make_ctx, tmp_path):
    ctx = make_ctx(fake_bodhan({}))
    src = tmp_path / "worksheet.md"
    src.write_text(TABLE_MD, encoding="utf-8")
    res = tool(ctx, "translate_text")(target_language="mr", path=str(src))
    assert res["ok"] and res["passed"], res
    assert res["checks"]["structure_preserved"] and res["checks"]["numbers_preserved"]
    assert "| 4 |" in res["translation"]  # numbers-only cell is copied, not sent to the translator
    assert (tmp_path / "work" / "artifacts" / "worksheet.mr.md").exists()


def test_translate_flags_dropped_numbers(make_ctx):
    ctx = make_ctx(fake_bodhan({}, drop_digits=True))
    res = tool(ctx, "translate_text")(target_language="hi", text="Add 25 and 3.")
    assert res["ok"] and not res["passed"]
    assert any(w.startswith("numbers_missing") for w in res["warnings"])


def test_translate_refuses_unsupported_language_with_list(make_ctx):
    ctx = make_ctx(fake_bodhan({}))
    res = tool(ctx, "translate_text")(target_language="bhb", text="Hello")
    assert res["ok"] is False and res["error"]["code"] == "unsupported_language"
    assert "hi" in res["error"]["message"]


def test_translate_empty_and_bad_arguments(make_ctx):
    ctx = make_ctx(fake_bodhan({}))
    assert tool(ctx, "translate_text")(target_language="hi", text="   ")["error"]["code"] == "empty_input"
    assert tool(ctx, "translate_text")(target_language="hi")["error"]["code"] == "bad_arguments"
    assert tool(ctx, "translate_text")(target_language="hi", text="a", path="x.md")["error"]["code"] == "bad_arguments"


def test_translate_rejects_video_path_as_wrong_type(make_ctx, tmp_path):
    ctx = make_ctx(fake_bodhan({}))
    video = tmp_path / "lesson.mp4"
    video.write_bytes(b"not really a video")
    res = tool(ctx, "translate_text")(target_language="hi", path=str(video))
    assert res["error"]["code"] == "wrong_media_type"


def test_speak_rejects_unknown_voice_style_and_language(make_ctx):
    ctx = make_ctx(fake_bodhan({}))
    speak = tool(ctx, "speak_text")
    assert speak(language="ta", text="வணக்கம்", voice="Nobody")["error"]["code"] == "unknown_voice"
    assert speak(language="ta", text="வணக்கம்", style="shouting")["error"]["code"] == "bad_arguments"
    assert speak(language="bhb", text="x")["error"]["code"] == "unsupported_language"


@needs_ffmpeg
def test_speak_verifies_by_back_transcription(make_ctx, tmp_path):
    state = {}
    ctx = make_ctx(fake_bodhan(state))
    res = tool(ctx, "speak_text")(language="hi", text="कक्षा नौ बजे शुरू होती है।", gender="female")
    assert res["ok"] and res["verified"] is True, res
    assert res["voice"] == "Kavya"
    assert res["audio_path"].endswith(".wav")
    label = json.loads(Path(res["audio_path"]).with_suffix(".json").read_text(encoding="utf-8"))
    assert "AI-generated" in label["label"]


@needs_ffmpeg
def test_speak_flags_mismatch_when_back_transcript_differs(make_ctx):
    state = {}
    ctx = make_ctx(fake_bodhan(state, transcript=lambda s: "पूरी तरह अलग बात यहाँ है"))
    res = tool(ctx, "speak_text")(language="hi", text="कक्षा नौ बजे शुरू होती है।")
    assert res["ok"] and res["verified"] is False
    assert any(w.startswith("speech_mismatch") for w in res["warnings"])


def test_transcribe_rejects_bad_language_and_wrong_media(make_ctx, tmp_path):
    ctx = make_ctx(fake_bodhan({}))
    pdf = tmp_path / "doc.pdf"
    Image.new("RGB", (50, 50), "white").save(pdf, "PDF")
    assert tool(ctx, "transcribe_audio")(path=str(pdf))["error"]["code"] == "wrong_media_type"
    assert tool(ctx, "transcribe_audio")(path=str(pdf), language="fr")["error"]["code"] == "unsupported_language"
    assert tool(ctx, "transcribe_audio")(path=str(tmp_path / "missing.wav"))["error"]["code"] == "file_not_found"


def test_ocr_flags_cut_off_pages_and_writes_markdown(make_ctx, tmp_path):
    state = {"finish": "length"}
    ctx = make_ctx(fake_bodhan(state))
    img = tmp_path / "page.png"
    Image.new("RGB", (60, 60), "white").save(img)
    res = tool(ctx, "ocr_document")(path=str(img))
    assert res["ok"] and res["pages"] == 1
    assert res["flagged_pages"] == [1] and not res["passed"]
    flags = res["page_results"][0]["flags"]
    assert any(f.startswith("cut_off") for f in flags) and any("low detection confidence" in f for f in flags)
    assert (tmp_path / "work" / "artifacts" / "page.ocr.md").read_text(encoding="utf-8").startswith("<!-- page 1 -->")


def test_ocr_rejects_audio(make_ctx, tmp_path):
    ctx = make_ctx(fake_bodhan({}))
    res = tool(ctx, "ocr_document")(path=str(tmp_path / "missing.png"))
    assert res["error"]["code"] == "file_not_found"


def test_list_languages_and_inspect_missing_file(make_ctx):
    ctx = make_ctx(fake_bodhan({}))
    langs = tool(ctx, "list_languages")(capability="translation")
    assert langs["ok"] and "ta" in langs["languages"] and "bhb" not in langs["languages"]
    assert tool(ctx, "list_languages")(capability="magic")["error"]["code"] == "bad_arguments"
    assert tool(ctx, "inspect_media")(path="/no/such/file.wav")["error"]["code"] == "file_not_found"


def test_detect_language_does_not_call_bodhan(make_ctx):
    state = {}
    ctx = make_ctx(fake_bodhan(state))
    res = tool(ctx, "detect_language")(text="வணக்கம்")
    assert res["candidate_languages"] == ["ta"]
    assert "translate_calls" not in state and ctx.client.ledger == []


def tone(path, seconds=3):
    import subprocess

    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=duration={seconds}", str(path)], check=True)
    return path


def test_cues_share_window_time_by_sentence_length():
    from bodhan_mcp.tools import cues_from_segments, to_srt

    cues = cues_from_segments([{"start": 0.0, "end": 10.0, "text": "Short one. A much longer second sentence here."}])
    assert len(cues) == 2 and cues[0]["start"] == 0.0 and cues[-1]["end"] == 10.0
    assert cues[0]["end"] < 5.0  # the shorter sentence gets less time
    unpunctuated = cues_from_segments([{"start": 0.0, "end": 20.0, "text": " ".join(["word"] * 60)}])
    assert len(unpunctuated) > 1 and all(len(c["text"]) <= 84 for c in unpunctuated) and unpunctuated[-1]["end"] == 20.0
    srt = to_srt([{"start": 3661.5, "end": 3662.25, "text": "x"}], "text")
    assert srt == "1\n01:01:01,500 --> 01:01:02,250\nx\n"


@needs_ffmpeg
def test_make_subtitles_writes_both_srt_files(make_ctx, tmp_path):
    state = {}
    ctx = make_ctx(fake_bodhan(state, transcript=lambda s: "मीटिंग 5 बजे है। कल मिलते हैं।"))
    res = tool(ctx, "make_subtitles")(path=str(tone(tmp_path / "note.wav")), target_language="en", source_language="hi")
    assert res["ok"] and res["cues"] == 2, res
    target = (tmp_path / "work" / "artifacts" / "note.en.srt").read_text(encoding="utf-8")
    assert target.startswith("1\n00:00:00,000 --> ") and "[en]" in target
    assert (tmp_path / "work" / "artifacts" / "note.hi.srt").exists()
    assert len(state["translate_calls"]) == 1  # one window, translated whole, then split into cues


@needs_ffmpeg
def test_make_subtitles_with_no_speech_is_an_error(make_ctx, tmp_path):
    ctx = make_ctx(fake_bodhan({}, transcript=lambda s: ""))
    res = tool(ctx, "make_subtitles")(path=str(tone(tmp_path / "quiet.wav")), target_language="en")
    assert res["error"]["code"] == "no_speech"


@needs_ffmpeg
def test_identify_audio_language_reads_the_script(make_ctx, tmp_path):
    ctx = make_ctx(fake_bodhan({}, transcript=lambda s: "నమస్కారం, ఎలా ఉన్నారు?"))
    res = tool(ctx, "identify_audio_language")(path=str(tone(tmp_path / "clip.wav")))
    assert res["ok"] and res["candidate_languages"] == ["te"] and res["dominant_script"] == "telugu"


def test_read_text_pages_through_a_file(make_ctx, tmp_path):
    ctx = make_ctx(fake_bodhan({}))
    f = tmp_path / "long.md"
    f.write_text("a" * 25, encoding="utf-8")
    first = tool(ctx, "read_text")(path=str(f), max_chars=10)
    assert first["text"] == "a" * 10 and first["next_offset"] == 10 and first["total_chars"] == 25
    last = tool(ctx, "read_text")(path=str(f), offset=20, max_chars=10)
    assert last["text"] == "a" * 5 and last["next_offset"] is None
    assert tool(ctx, "read_text")(path=str(tmp_path / "x.wav"))["error"]["code"] == "file_not_found"


def test_output_name_controls_file_name(make_ctx):
    ctx = make_ctx(fake_bodhan({}))
    res = tool(ctx, "translate_text")(target_language="ta", text="Hello there.", output_name="summary")
    assert res["output_path"].endswith("summary.ta.md")


def test_read_text_points_indic_text_to_translation(tmp_path):
    tools = {f.__name__: f for f in make_tools(Context(client=None, work_dir=tmp_path, tmp_root=tmp_path / ".work"))}
    hindi = tmp_path / "note.hi.srt"
    hindi.write_text("1\n00:00:00,000 --> 00:00:02,000\nनमस्ते प्रिया, मैं रवि बोल रहा हूँ\n", encoding="utf-8")
    english = tmp_path / "note.en.md"
    english.write_text("Hello Priya, this is Ravi.", encoding="utf-8")
    assert "translate_text" in tools["read_text"](path=str(hindi))["note"]
    assert "note" not in tools["read_text"](path=str(english))
