"""Per-tool behaviour with the Bodhan HTTP layer mocked: chunking, limits, structured errors, caching."""
from __future__ import annotations

from pathlib import Path

import pytest
from conftest import HINDI, needs_ffmpeg, needs_poppler, tone_wav
from PIL import Image

from bodhan_mcp import config, tools

ERROR_KEYS = {"error_code", "message", "retryable", "hint"}


def assert_error(r, code):
    assert r.ok is False and r.error is not None, r
    assert set(r.error.model_dump()) == ERROR_KEYS
    assert r.error.error_code == code, r.error
    assert r.error.hint


# ---------------------------------------------------------------- structured errors on bad input (no API needed)
def test_every_tool_returns_structured_errors_and_never_raises(tmp_path):
    empty = tmp_path / "empty.wav"
    empty.write_bytes(b"")
    txt = tmp_path / "notes.txt"
    txt.write_text("hello world", encoding="utf-8")
    assert_error(tools.inspect_media("/no/such/file.wav"), "FILE_NOT_FOUND")
    assert_error(tools.inspect_media(""), "INVALID_ARGUMENT")
    assert_error(tools.detect_script_language("   "), "EMPTY_INPUT")
    assert_error(tools.list_voices("fr"), "UNSUPPORTED_LANGUAGE")
    assert_error(tools.transcribe_audio("/no/such.wav"), "FILE_NOT_FOUND")
    assert_error(tools.transcribe_audio(str(empty)), "EMPTY_INPUT")
    assert_error(tools.transcribe_audio(str(txt)), "UNSUPPORTED_FILE_TYPE")
    assert_error(tools.transcribe_audio(str(txt), "hi"), "UNSUPPORTED_FILE_TYPE")
    assert_error(tools.translate_text("hello", "fr"), "UNSUPPORTED_LANGUAGE")
    assert_error(tools.translate_text("hello", "Spanish"), "UNSUPPORTED_LANGUAGE")
    assert_error(tools.translate_text("", "hi"), "EMPTY_INPUT")
    assert_error(tools.translate_text("missing.txt", "hi"), "FILE_NOT_FOUND")
    assert_error(tools.translate_text("x" * (config.MAX_TEXT_CHARS + 1), "hi"), "INPUT_TOO_LARGE")
    assert_error(tools.translate_text("hello", "hi", script="weird"), "INVALID_ARGUMENT")
    assert_error(tools.synthesize_speech("hello", "fr"), "UNSUPPORTED_LANGUAGE")
    assert_error(tools.synthesize_speech(" ", "hi"), "EMPTY_INPUT")
    assert_error(tools.synthesize_speech("hello there", "ta"), "TEXT_LANGUAGE_MISMATCH")
    assert_error(tools.synthesize_speech(HINDI, "hi", voice="Nobody"), "UNSUPPORTED_VOICE")
    assert_error(tools.synthesize_speech(HINDI, "hi", style="angry-pirate"), "INVALID_ARGUMENT")
    assert_error(tools.synthesize_speech(HINDI * 600, "hi"), "INPUT_TOO_LARGE")
    assert_error(tools.ocr_document("/no/such.png"), "FILE_NOT_FOUND")
    assert_error(tools.ocr_document(str(txt)), "UNSUPPORTED_FILE_TYPE")
    assert_error(tools.verify_audio("/no.wav", "x", "hi"), "FILE_NOT_FOUND")
    assert_error(tools.verify_translation(str(txt), "/no.md"), "FILE_NOT_FOUND")
    assert_error(tools.verify_translation(str(txt), str(txt), "fr"), "UNSUPPORTED_LANGUAGE")


def test_internal_exception_is_caught_not_raised(monkeypatch):
    monkeypatch.setattr(tools, "detect_script_language", tools.guarded(tools.DetectLanguageResult)(lambda _x: 1 / 0))
    r = tools.detect_script_language("x")
    assert_error(r, "INTERNAL_ERROR")


def test_language_aliases_accepted():
    assert tools.list_voices("Tamil").language == "ta"
    assert tools.list_voices("ta-in").language == "ta"
    v = tools.list_voices("hi", "female")
    assert [x.name for x in v.voices] == ["Kavya", "Suhani"]


def test_inspect_media_detects_mislabelled_pdf_and_text(tmp_path):
    pdf = tmp_path / "scan.jpg"
    Image.new("RGB", (40, 30), "white").save(tmp_path / "p.pdf")
    (tmp_path / "p.pdf").replace(pdf)  # a PDF named .jpg
    r = tools.inspect_media(str(pdf))
    assert r.media.kind == "pdf" and r.media.extension_mismatch and r.media.pages == 1
    t = tmp_path / "a.txt"
    t.write_text("வணக்கம்", encoding="utf-8")
    assert tools.inspect_media(str(t)).media.script_language["language"] == "ta"
    d = tools.inspect_media(str(tmp_path))
    assert len(d.entries) == 2


# ---------------------------------------------------------------- transcribe: windows, stitching, cache
@needs_ffmpeg
def test_transcribe_splits_long_audio_into_windows_under_30s_and_stitches(tmp_path, bodhan):
    src = tone_wav(tmp_path / "long.wav", [(20, True), (1, False), (20, True), (1, False), (20, True)])
    r = tools.transcribe_audio(str(src), "hi")
    assert r.ok, r.error
    assert r.windows == 3 and bodhan.count("/v1/audio/transcriptions") == 3
    assert all(c["seconds"] <= 30 for c in bodhan.calls)
    assert [s.start_s for s in r.segments] == sorted(s.start_s for s in r.segments)
    assert abs(r.segments[-1].end_s - 62.0) < 0.1
    assert Path(r.transcript_path).read_text(encoding="utf-8").count(HINDI) == 3
    assert "-->" in Path(r.srt_path).read_text(encoding="utf-8")
    assert r.language == "hi" and abs(r.cost_inr - 62 / 60 * 0.10) < 0.002
    assert bodhan.calls[0]["auth"].endswith("test-key-BODHAN_TRANSCRIBE_KEY")  # per-model key


@needs_ffmpeg
def test_transcribe_cache_hit_means_no_second_api_call(tmp_path, bodhan):
    src = tone_wav(tmp_path / "a.wav", [(3, True)])
    first = tools.transcribe_audio(str(src), "hi")
    n = len(bodhan.calls)
    second = tools.transcribe_audio(str(src), "hi")
    assert first.ok and second.ok and len(bodhan.calls) == n
    assert second.cost_inr == 0 and second.cache_hits == 1 and first.cost_inr > 0


@needs_ffmpeg
def test_transcribe_skips_silent_windows_without_calling_model(tmp_path, bodhan):
    src = tone_wav(tmp_path / "s.wav", [(5, False)])
    r = tools.transcribe_audio(str(src), "hi")
    assert r.ok and bodhan.count("/v1/audio/transcriptions") == 0 and r.skipped_silent_windows == 1 and r.segments == []
    assert r.warnings


# ---------------------------------------------------------------- translate: chunking, tables, SRT, math
def test_translate_chunks_long_text_inside_context_and_keeps_order(tmp_path, bodhan):
    text = "\n\n".join(f"Paragraph {i}. " + "This is a sentence. " * 30 for i in range(12))
    r = tools.translate_text(text, "hi")
    assert r.ok, r.error
    sent = [c["json"]["messages"][0]["content"] for c in bodhan.calls]
    assert len(sent) == r.chunks > 3 and all(len(s) <= config.TRANSLATE_CHUNK_MAX_CHARS for s in sent)
    assert all(c["json"]["model"] == "indic-translate" and c["json"]["target_language_code"] == "hi" for c in bodhan.calls)
    assert Path(r.output_path).read_text(encoding="utf-8").count(HINDI) == r.chunks


def test_translate_same_input_twice_hits_cache(bodhan):
    tools.translate_text("Hello there.", "hi")
    n = len(bodhan.calls)
    r = tools.translate_text("Hello there.", "hi")
    assert len(bodhan.calls) == n and r.cache_hits == 1 and r.cost_inr == 0


def test_translate_table_kept_and_cellwise_fallback(bodhan, monkeypatch):
    md = "Intro text here.\n\n| Item | Qty |\n|---|---|\n| Pencil | 12 |\n| Notebook | 5 |\n"
    # model returns the table with the SAME shape -> accepted as one call
    bodhan.translate_fn = lambda text, _t: "| वस्तू | संख्या |\n|---|---|\n| पेन्सिल | 12 |\n| वही | 5 |" if text.startswith("|") else "परिचय मजकूर"
    r = tools.translate_text(md, "mr")
    out = Path(r.output_path).read_text(encoding="utf-8")
    assert r.ok and out.count("|---|---|") == 1 and "| पेन्सिल | 12 |" in out
    # model destroys the table -> tool falls back to cell-by-cell and still returns a valid table
    bodhan.calls.clear()
    bodhan.translate_fn = lambda _text, _t: "फक्त मजकूर"
    r2 = tools.translate_text("Another intro.\n\n| Fruit | Kg |\n|---|---|\n| Mango | 3 |\n| Apple | 9 |\n", "mr")  # new text: not served from cache
    out2 = Path(r2.output_path).read_text(encoding="utf-8")
    table_lines = [ln for ln in out2.splitlines() if ln.startswith("|")]
    assert r2.ok and len(table_lines) == 4 and all(ln.count("|") == 3 for ln in table_lines)
    assert any("cell by cell" in w for w in r2.warnings)


def test_translate_srt_keeps_timestamps_and_cue_count(tmp_path, bodhan):
    srt = tmp_path / "a.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nनमस्ते\n\n2\n00:00:02,500 --> 00:00:04,000\nधन्यवाद\n", encoding="utf-8")
    r = tools.translate_text(str(srt), "en")
    out = Path(r.output_path)
    assert r.ok and out.suffix == ".srt"
    text = out.read_text(encoding="utf-8")
    assert "00:00:02,500 --> 00:00:04,000" in text and text.count("-->") == 2
    v = tools.verify_translation(str(srt), str(out), "en")
    assert v.ok and v.passed, v.checks


def test_translate_preserves_latex_via_placeholders(bodhan):
    r = tools.translate_text("The area is $\\pi r^2$ here.", "hi")
    assert r.ok and "$\\pi r^2$" in Path(r.output_path).read_text(encoding="utf-8")


def test_translate_same_language_is_free_noop(bodhan):
    r = tools.translate_text("hello", "en", source_lang="en")
    assert r.ok and bodhan.calls == [] and r.cost_inr == 0 and r.warnings


def test_translate_indic_pair_adds_pivot_note_and_pivot_costs_two_hops(bodhan):
    r = tools.translate_text(HINDI, "ta", source_lang="hi")
    assert r.ok and "English" in r.pivot_note and len(bodhan.calls) == 1
    bodhan.calls.clear()
    r2 = tools.translate_text(HINDI + " और", "ta", source_lang="hi", pivot_via_english=True)
    assert r2.ok and [c["json"]["target_language_code"] for c in bodhan.calls] == ["en", "ta"]


# ---------------------------------------------------------------- synthesize: splitting, join, labels, caching
@needs_ffmpeg
def test_synthesize_splits_at_sentences_joins_and_labels_ai(bodhan, tmp_path):
    text = " ".join(f"यह वाक्य संख्या {i} है।" for i in range(40))
    r = tools.synthesize_speech(text, "hi", gender="male")
    assert r.ok, r.error
    sent = [c["json"]["input"] for c in bodhan.calls]
    assert len(sent) == r.requests > 4 and all(len(s) <= config.TTS_REQUEST_MAX_CHARS for s in sent)
    assert all(c["json"]["voice"] == "Amit" and '"lang": "hi"' in c["json"]["instructions"] for c in bodhan.calls)
    assert Path(r.audio_path).suffix == ".mp3" and r.duration_s > r.requests * 0.9
    import json
    import subprocess
    meta = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", r.audio_path], capture_output=True, text=True).stdout
    assert "AI-generated" in json.loads(meta)["format"]["tags"]["comment"]
    assert abs(r.cost_inr - len(text) / 10000 * 6) < 0.01
    n = len(bodhan.calls)
    assert tools.synthesize_speech(text, "hi", gender="male").cost_inr == 0 and len(bodhan.calls) == n  # same input: cache


@needs_ffmpeg
def test_synthesize_voice_resolution_and_markdown_stripped(bodhan):
    md = "# शीर्षक\n\n| A | B |\n|---|---|\n| क | ख |\n\n**मोटा** पाठ।"
    r = tools.synthesize_speech(md, "bn" if False else "hi", voice="suhani")
    assert r.ok and r.voice == "Suhani"
    spoken = bodhan.calls[0]["json"]["input"]
    assert "#" not in spoken and "|" not in spoken and "*" not in spoken and "---" not in spoken


# ---------------------------------------------------------------- OCR: images, PDF pagination, flags
def test_ocr_image_returns_markdown_file_and_flags(tmp_path, bodhan):
    img = tmp_path / "p.png"
    Image.new("RGB", (300, 200), "white").save(img)
    r = tools.ocr_document(str(img))
    assert r.ok and r.pages_processed == 1 and "| A | B |" in Path(r.markdown_path).read_text(encoding="utf-8")
    assert r.cost_inr == pytest.approx(0.20) and bodhan.calls[0]["json"]["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    bodhan.ocr_text, bodhan.ocr_conf = "", 0.3
    img2 = tmp_path / "q.png"
    Image.new("RGB", (310, 200), "white").save(img2)
    r2 = tools.ocr_document(str(img2))
    assert not r2.markdown_path and r2.pages[0].flags == ["empty", "low_confidence"] and r2.warnings


@needs_poppler
def test_ocr_pdf_is_paginated_page_range_and_limits(tmp_path, bodhan):
    pages = [Image.new("RGB", (200, 280), (255 - i * 20, 255, 255)) for i in range(4)]
    pdf = tmp_path / "doc.pdf"
    pages[0].save(pdf, save_all=True, append_images=pages[1:])
    r = tools.ocr_document(str(pdf))
    assert r.ok and r.total_pages_in_file == 4 and r.pages_processed == 4 and bodhan.count("/v1/chat/completions") == 4
    assert Path(r.markdown_path).read_text(encoding="utf-8").count("\n---\n") == 3
    bodhan.calls.clear()
    r2 = tools.ocr_document(str(pdf), page_range="2-3")
    assert r2.pages_processed == 2 and [p.page for p in r2.pages] == [2, 3]
    assert_error(tools.ocr_document(str(pdf), page_range="9"), "INVALID_ARGUMENT")
    assert_error(tools.ocr_document(str(pdf), page_range="x"), "INVALID_ARGUMENT")


def test_ocr_pdf_page_cap(tmp_path, bodhan, monkeypatch):
    pages = [Image.new("RGB", (100, 100), "white") for _ in range(3)]
    pdf = tmp_path / "doc.pdf"
    pages[0].save(pdf, save_all=True, append_images=pages[1:])
    monkeypatch.setattr(config, "MAX_OCR_PAGES", 2)
    assert_error(tools.ocr_document(str(pdf)), "INPUT_TOO_LARGE")
    assert bodhan.calls == []  # refused before spending anything


# ---------------------------------------------------------------- verification tools
def test_verify_translation_catches_structure_loss_and_wrong_script(tmp_path):
    src = tmp_path / "s.md"
    src.write_text("# Title\n\nPrice is 40 rupees.\n\n| A | B |\n|---|---|\n| x | 5 |\n", encoding="utf-8")
    good = tmp_path / "g.md"
    good.write_text("# शीर्षक\n\nकिंमत ४० रुपये आहे.\n\n| अ | ब |\n|---|---|\n| क | ५ |\n", encoding="utf-8")
    assert tools.verify_translation(str(src), str(good), "mr").passed
    bad = tmp_path / "b.md"
    bad.write_text("Price is forty rupees.", encoding="utf-8")
    r = tools.verify_translation(str(src), str(bad), "mr")
    failed = {c.name for c in r.checks if not c.ok}
    assert not r.passed and {"headings", "table_rows", "numbers_preserved", "target_script"} <= failed
    same = tools.verify_translation(str(src), str(src), "en")
    assert "not_identical" in {c.name for c in same.checks if not c.ok}


@needs_ffmpeg
def test_verify_audio_pass_and_failure_flags(tmp_path, bodhan):
    src = tone_wav(tmp_path / "a.wav", [(3, True)])
    ok = tools.verify_audio(str(src), HINDI, "hi")
    assert ok.ok and ok.passed and ok.cer == 0
    bodhan.transcript = "नमस्ते"  # model heard only a fraction -> omission
    tone_wav(tmp_path / "b.wav", [(3.5, True)])
    bad = tools.verify_audio(str(tmp_path / "b.wav"), HINDI, "hi")
    assert not bad.passed and "omission" in bad.flags
    bodhan.transcript = "नमस्ते आज हम " * 6  # looping
    tone_wav(tmp_path / "c.wav", [(4, True)])
    rep = tools.verify_audio(str(tmp_path / "c.wav"), "नमस्ते आज हम गणित की कक्षा शुरू करेंगे", "hi")
    assert "repetition" in rep.flags and not rep.passed


@needs_ffmpeg
def test_synthesize_splits_a_stalled_request_instead_of_repeating_it(bodhan):
    bodhan.stall_speech_over = 80
    text = "यह एक लंबा वाक्य है, जिसमें कई शब्द हैं और यह बहुत देर तक चलता रहता है, इसलिए इसे दो हिस्सों में बाँटना पड़ेगा ताकि आवाज़ बन सके।"
    r = tools.synthesize_speech(text, "hi")
    assert r.ok, r.error
    sizes = [len(c["json"]["input"]) for c in bodhan.calls]
    assert r.requests >= 2 and sizes[0] > 80 and all(n <= 80 for n in sizes[1:])  # first try stalled; halves succeeded
    assert sizes.count(sizes[0]) == 1  # the stalled input was NOT retried unchanged


@needs_ffmpeg
def test_content_beats_extension_for_audio_hiding_behind_txt(tmp_path, bodhan):
    src = tone_wav(tmp_path / "real.wav", [(3, True)])
    fake = tmp_path / "voice_note.txt"
    fake.write_bytes(src.read_bytes())
    info = tools.inspect_media(str(fake)).media
    assert info.kind == "audio" and info.extension_mismatch and info.suggested_tools == ["transcribe_audio"]
    r = tools.transcribe_audio(str(fake), "hi")
    assert r.ok and r.segments


def test_translating_text_already_in_the_target_language_is_a_free_noop(bodhan):
    bn = "গ্রামের গ্রন্থাগারটি সকাল ন'টায় খোলে এবং সন্ধ্যায় বন্ধ হয়। সবাই স্বাগত, এবং সদস্যপদ বিনামূল্যে আছে।"
    r = tools.translate_text(bn, "bn")
    assert r.ok and r.noop and bodhan.calls == [] and r.cost_inr == 0 and Path(r.output_path).read_text(encoding="utf-8").strip() == bn
    assert tools.translate_text(TAMIL_TEXT, "ta").noop                       # distinctive script
    assert not tools.translate_text(HINDI, "mr").noop                        # Hindi -> Marathi is a real translation
    assert not tools.translate_text("kal school band rahega", "en").noop     # romanised Indic is not "already English"


TAMIL_TEXT = "வணக்கம் இன்று வகுப்பு பத்து மணிக்கு தொடங்கும்"


def test_verify_translation_accepts_identical_text_already_in_target_language(tmp_path):
    bn = "গ্রামের গ্রন্থাগারটি সকাল ন'টায় খোলে এবং সন্ধ্যায় বন্ধ হয়। সবাই স্বাগত, এবং সদস্যপদ বিনামূল্যে আছে।"
    f = tmp_path / "bn.txt"
    f.write_text(bn, encoding="utf-8")
    assert tools.verify_translation(str(f), str(f), "bn").passed
    assert not tools.verify_translation(str(f), str(f), "ta").passed      # same text claimed to be Tamil: still caught
