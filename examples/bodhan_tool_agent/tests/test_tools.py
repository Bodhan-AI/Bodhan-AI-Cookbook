"""Per-tool chunking and limit handling, plus structured errors, against the fake Bodhan API."""

from __future__ import annotations

import json

import pytest

from bodhan_mcp.tools import helpers, media, ocr, speak, transcribe, translate, verify
from bodhan_mcp.tools.speak import speakable_chunks
from bodhan_mcp.tools.translate import split_long

from conftest import HINDI, make_wav


def test_transcribe_splits_long_audio_into_windows(rt, fake, tmp_path):
    wav = make_wav(tmp_path / "long.wav", 70)
    (t,) = transcribe.make_tools(rt)
    res = t(str(wav), language="hi")
    assert res.ok and res.windows in (3, 4)  # 70 s -> windows of <= 25 s (cuts snap to quiet points)
    assert len([c for c in fake.calls if c[0] == "/v1/audio/transcriptions"]) == res.windows
    saved = json.loads((rt.work_dir / res.transcript_path).read_text(encoding="utf-8"))
    assert saved["segments"][-1]["end_s"] == pytest.approx(70, abs=0.1) and len(saved["segments"]) == res.windows and all(g["end_s"] - g["start_s"] <= 25.01 for g in saved["segments"])
    assert res.cost_inr == pytest.approx(70 / 60 * 0.10, abs=0.01)


def test_transcribe_detects_language_and_flags_ambiguity(rt, tmp_path):
    (t,) = transcribe.make_tools(rt)
    res = t(str(make_wav(tmp_path / "a.wav", 5)))
    assert res.ok and res.language == "hi" and res.language_was_detected and res.language_ambiguous  # Devanagari: hi/mr/ne


def test_transcribe_same_audio_twice_is_cached(rt, fake, tmp_path):
    wav = make_wav(tmp_path / "a.wav", 5)
    (t,) = transcribe.make_tools(rt)
    t(str(wav), language="hi")
    second = t(str(wav), language="hi", output_name="again")
    assert second.cost_inr == 0 and len(fake.calls) == 1


@pytest.mark.parametrize("kwargs,code", [
    ({"path": "missing.wav"}, "file_not_found"),
    ({"path": "{wav}", "language": "French"}, "unsupported_language"),
])
def test_transcribe_structured_errors(rt, tmp_path, kwargs, code):
    wav = make_wav(tmp_path / "a.wav", 2)
    kwargs = {k: (str(wav) if v == "{wav}" else v) for k, v in kwargs.items()}
    (t,) = transcribe.make_tools(rt)
    res = t(**kwargs)
    assert not res.ok and res.error.code == code and res.error.hint


def test_transcribe_garbage_file_does_not_crash(rt, tmp_path):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not audio at all")
    (t,) = transcribe.make_tools(rt)
    res = t(str(bad), language="hi")
    assert not res.ok and res.error.code in ("undecodable_audio", "unsupported_format")


def test_split_long_respects_limit():
    text = ("This is a sentence. " * 200).strip()
    parts = split_long(text, 300)
    assert all(len(p) <= 300 for p in parts) and " ".join(parts).split() == text.split()
    giant = "x" * 1000  # no spaces at all: hard wrap
    assert all(len(p) <= 300 for p in split_long(giant, 300))


def test_translate_preserves_markdown_table_and_structure(rt, fake, tmp_path):
    src = "# Worksheet\n\nName the animals.\n\n| Animal | Legs |\n|---|---|\n| Cat | 4 |\n| Bird | 2 |\n\n- one\n- two"
    (t,) = translate.make_tools(rt)
    res = t("hi", text=src)
    out = (rt.work_dir / res.translation_path).read_text(encoding="utf-8")
    assert res.ok and res.verified
    assert "|---|---|" in out and "| 4 |" in out and "| 2 |" in out  # separator and numeric cells untouched
    assert out.count("|") == src.count("|") and out.startswith("# ") and "\n- " in out
    sent = [c[1]["text"] for c in fake.calls if c[0] == "/translate"]
    assert len(sent) == len(set(sent))  # identical pieces never sent twice


def test_translate_long_text_chunks_under_window(rt, fake):
    long_text = "\n\n".join(("Sentence number %d is here. " % i) * 100 for i in range(5))
    (t,) = translate.make_tools(rt)
    res = t("hi", text=long_text)
    assert res.ok and res.chunks > 5
    assert all(len(c[1]["text"]) <= rt.settings.limits.translate_max_chars for c in fake.calls)


def test_translate_unsupported_and_bad_args(rt):
    (t,) = translate.make_tools(rt)
    assert t("French", text="hi").error.code == "unsupported_language"
    assert t("hi").error.code == "invalid_input"
    assert t("hi", text="a", text_path="b").error.code == "invalid_input"
    assert t("hi", text="   ").error.code == "empty_input"


def test_translate_coverage_flags_wrong_script(rt, fake):
    (t,) = translate.make_tools(rt)
    assert t("hi", text="Hello there my friend").verified
    fake.translate_in_latin = True
    bad = t("hi", text="Good morning everyone")
    assert bad.ok and not bad.verified and bad.coverage.target_script_ok is False and bad.warnings


def test_translate_reports_partial_failure(rt, fake):
    fake.fail_with["/translate"] = 400
    (t,) = translate.make_tools(rt)
    res = t("hi", text="One.\n\nTwo.")
    assert not res.ok and res.error.code == "bad_request" and res.error.details["pieces_failed"] == 2


def test_speak_chunks_are_short_and_complete():
    text = "पहला वाक्य यहाँ है। " * 40
    chunks = speakable_chunks(text, 220)
    assert all(len(c) <= 220 for c in chunks) and len(chunks) > 3
    assert " ".join(chunks).split() == text.split()


def test_speak_labels_ai_generated_and_joins_chunks(rt, fake):
    (s,) = speak.make_tools(rt)
    res = s("hi", text=" ".join(f"Sentence {i} is spoken here." for i in range(30)), gender="male")
    assert res.ok and res.ai_generated and "AI-generated" in res.label and res.voice == "Amit"
    assert res.requests == len([c for c in fake.calls if c[0] == "/v1/audio/speech"]) > 1
    audio_file = rt.work_dir / res.audio_path
    assert audio_file.exists() and audio_file.with_name(audio_file.name + ".ai-generated.txt").exists()
    assert res.duration_s > res.requests * 1.0  # pauses added between requests
    assert res.cost_inr == pytest.approx(res.chars / 10000 * 6, rel=0.01)


def test_speak_errors(rt):
    (s,) = speak.make_tools(rt)
    assert s("fr", text="bonjour").error.code == "unsupported_language"
    assert s("hi", text="नमस्ते", voice="Zorro").error.code == "unknown_voice"  # fixed library, no cloning
    assert s("hi", text="...").error.code == "empty_input"
    assert s("hi", text="x", style="grumpy").error.code == "invalid_input"


def test_speak_stops_before_overspending(rt, fake):
    rt.ledger.max_inr = 0.01
    (s,) = speak.make_tools(rt)
    res = s("hi", text="This sentence costs more than one paisa to speak aloud, certainly.")
    assert not res.ok and res.error.code == "budget_exceeded" and not any(c[0] == "/v1/audio/speech" for c in fake.calls)


def test_verify_audio_passes_and_detects_omission_and_repetition(rt, fake, tmp_path):
    wav = make_wav(tmp_path / "s.wav", 4)
    (v,) = verify.make_tools(rt)
    ok = v(str(wav), "hi", expected_text=HINDI)
    assert ok.ok and ok.passed and ok.expected_word_coverage == 1.0
    fake.stt_text = "नमस्ते"
    omitted = v(str(wav), "hi", expected_text=HINDI + " और बहुत सारे शब्द जो कभी बोले नहीं गए")
    assert omitted.passed is False and omitted.omission_suspected
    fake.stt_text = (HINDI + " ") * 4
    wav2 = make_wav(tmp_path / "s2.wav", 6)
    repeated = v(str(wav2), "hi", expected_text=HINDI)
    assert repeated.passed is False and repeated.repetition_suspected


def test_ocr_multipage_pdf(rt, fake, tmp_path):
    from PIL import Image

    pdf = tmp_path / "doc.pdf"
    pages = [Image.new("RGB", (200, 200), c) for c in ("white", "gray", "silver")]
    pages[0].save(pdf, save_all=True, append_images=pages[1:])
    (o,) = ocr.make_tools(rt)
    res = o(str(pdf))
    assert res.ok and res.pages_processed == 3 and len(fake.calls) == 3
    assert res.cost_inr == pytest.approx(0.6)
    assert (rt.work_dir / res.markdown_path).read_text(encoding="utf-8").count("---\n\n#") == 2  # '---' only as page separators


def test_ocr_rejects_audio(rt, tmp_path):
    (o,) = ocr.make_tools(rt)
    res = o(str(make_wav(tmp_path / "a.wav", 1)))
    assert not res.ok and res.error.code == "unsupported_format"


def test_inspect_media_kinds(rt, tmp_path):
    (i,) = media.make_tools(rt)
    wav = make_wav(tmp_path / "a.wav", 3)
    r = i(str(wav))
    assert r.kind == "audio" and r.duration_s == pytest.approx(3, abs=0.1)
    txt = tmp_path / "t.txt"
    txt.write_text("வணக்கம் உலகம்", encoding="utf-8")
    t = i(str(txt))
    assert t.kind == "text" and t.detected_language == "ta"
    assert i(str(tmp_path / "x.xyz")).error.code == "file_not_found"
    (tmp_path / "x.xyz").write_text("?")
    assert i(str(tmp_path / "x.xyz")).kind == "unknown"


def test_subtitles_from_transcript(rt, fake, tmp_path):
    (t,) = transcribe.make_tools(rt)
    tr = t(str(make_wav(tmp_path / "v.wav", 60)), language="hi")
    tools = {f.__name__: f for f in helpers.make_tools(rt)}
    res = tools["create_subtitles"](tr.transcript_path, target_language="en")
    srt = (rt.work_dir / res.srt_path).read_text(encoding="utf-8")
    assert res.ok and res.verified and res.cues == 3 and "00:00:00,000 --> " in srt and "English translation" in srt
    assert tools["create_subtitles"](tr.text_path.replace(".txt", ".txt")).error.code == "invalid_input"


def test_helpers_voices_and_language_support(rt):
    tools = {f.__name__: f for f in helpers.make_tools(rt)}
    assert tools["list_voices"]("ta").voices["female"] == ["Anitha"]
    assert tools["list_voices"]("French").error.code == "unsupported_language"
    assert tools["check_language_support"]("French").translation is False
    assert tools["check_language_support"]("Tamil").speech_synthesis is True
    assert tools["write_artifact"]("../../evil.md", "hello").path.startswith("artifacts/")  # cannot escape artifacts/
    assert tools["read_text_file"]("nope.txt").error.code == "file_not_found"
