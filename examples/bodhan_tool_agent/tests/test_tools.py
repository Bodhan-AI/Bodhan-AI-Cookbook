"""Tests for each tool's chunking, limit handling, checks and errors (fake API, no network)."""

import numpy as np
from conftest import FakeResponse, load, needs_ffmpeg, silence, tone, write_wav

from bodhan_mcp import config, media
from bodhan_mcp.tools import call_tool

# ---- pause detection and audio windows --------------------------------------


def test_windows_never_exceed_limit_and_cut_in_pauses():
    audio = np.concatenate([tone(20), silence(1), tone(20), silence(1), tone(20)])
    windows = media.speech_windows(audio, 16000, max_s=28.0)
    assert len(windows) == 3
    assert all(b - a <= 28.0 for a, b in windows)
    assert abs(windows[1][0] - 21.0) < 0.3  # the second window starts after the first pause


def test_long_speech_without_pauses_is_still_cut_under_limit():
    windows = media.speech_windows(tone(70), 16000, max_s=28.0)
    assert len(windows) >= 3 and all(b - a <= 28.0 for a, b in windows)
    assert abs(sum(b - a for a, b in windows) - 70) < 1.0  # nothing dropped


def test_silence_produces_no_windows():
    assert media.speech_windows(silence(10), 16000) == []


def test_short_neighbouring_regions_are_packed_into_one_window():
    audio = np.concatenate([tone(3), silence(0.8), tone(3), silence(0.8), tone(3)])
    assert len(media.speech_windows(audio, 16000, max_s=28.0)) == 1


# ---- transcribe_audio --------------------------------------------------------


@needs_ffmpeg
def test_transcribe_long_audio_is_chunked_and_timed(ctx, fake, inputs):
    ctx.client.settings.max_parallel = 1  # keep the fake's queued answers in order
    write_wav(inputs / "note.wav", np.concatenate([tone(20), silence(1), tone(20), silence(1), tone(20)]))
    fake.transcripts = ["पहला भाग", "दूसरा भाग", "तीसरा भाग"]
    result = call_tool(ctx, "transcribe_audio", {"path": "note.wav"})
    assert result["ok"] and result["speech_windows"] == 3 and result["segments"] == 3
    assert fake.count("/v1/audio/transcriptions") == 3
    assert result["detected_script"] == "Devanagari" and result["language_candidates"][0] == "hi"
    segments = load(ctx.workspace.resolve_input(result["segments_path"]))["segments"]
    assert [s["text"] for s in segments] == ["पहला भाग", "दूसरा भाग", "तीसरा भाग"]
    assert segments[0]["start"] < 0.5 and 20.5 < segments[1]["start"] < 21.5
    assert abs(ctx.client.ledger.spent_inr - 60 / 60 * config.PRICE_TRANSCRIBE_PER_MIN) < 0.01


@needs_ffmpeg
def test_a_few_stray_letters_for_noise_count_as_no_speech(ctx, fake, inputs):
    write_wav(inputs / "room.wav", tone(8))
    fake.transcripts = ["हाँ"]  # what the model returned for room noise in the live test
    result = call_tool(ctx, "transcribe_audio", {"path": "room.wav"})
    assert result["ok"] and result["no_speech"] and "noise, not speech" in result["warning"] and not ctx.produced


@needs_ffmpeg
def test_transcribe_silent_file_makes_no_model_call(ctx, fake, inputs):
    write_wav(inputs / "quiet.wav", silence(5))
    result = call_tool(ctx, "transcribe_audio", {"path": "quiet.wav"})
    assert result["ok"] and result["segments"] == 0 and "No speech" in result["warning"]
    assert result["no_speech"] and "transcript_path" not in result and not ctx.produced  # no empty file that looks like a result
    assert fake.count("/v1/audio/transcriptions") == 0


def test_transcribe_rejects_wrong_file_type_and_unknown_language(ctx, inputs):
    (inputs / "notes.txt").write_text("hello", encoding="utf-8")
    assert call_tool(ctx, "transcribe_audio", {"path": "notes.txt"})["error"]["code"] == "unsupported_file_type"
    write_wav(inputs / "a.wav", tone(1))
    error = call_tool(ctx, "transcribe_audio", {"path": "a.wav", "language": "French"})["error"]
    assert error["code"] == "unsupported_language" and "hi (Hindi)" in error["hint"]


# ---- translate_text ----------------------------------------------------------


def test_translate_long_document_is_chunked_and_passes_checks(ctx, fake, inputs):
    paragraphs = [" ".join(f"Sentence {i} of paragraph {p} has 25 words." for i in range(60)) for p in range(4)]
    (inputs / "doc.md").write_text("\n\n".join(paragraphs), encoding="utf-8")
    result = call_tool(ctx, "translate_text", {"source_path": "doc.md", "target_language": "Tamil"})
    assert result["ok"] and result["target_language"] == "ta" and result["checks"]["passed"]
    sent = [kw["json"]["messages"][0]["content"] for path, kw in fake.calls if kw.get("json", {}).get("model") == "indic-translate"]
    assert len(sent) > 4 and all(len(t) <= config.MAX_TRANSLATE_CHARS for t in sent)
    assert result["checks"]["number_mismatches"] == 0


def test_translate_keeps_table_layout_and_leaves_code_alone(ctx, fake, inputs):
    doc = "Marks list\n\n| Name | Marks |\n| --- | --- |\n| Asha | 90 |\n| Ravi | 85 |\n\n```\nprint('hello')\n```"
    (inputs / "sheet.md").write_text(doc, encoding="utf-8")
    result = call_tool(ctx, "translate_text", {"source_path": "sheet.md", "target_language": "mr"})
    out = ctx.workspace.resolve_input(result["output_path"]).read_text(encoding="utf-8")
    assert "print('hello')" in out  # code untouched
    table = [line for line in out.splitlines() if line.startswith("|")]
    assert len(table) == 4 and all(line.count("|") == 3 for line in table)
    assert "90" in out and "85" in out


def test_translate_rebuilds_table_cell_by_cell_when_model_breaks_it(ctx, fake, inputs):
    fake.translate_break_tables = True
    (inputs / "t.md").write_text("| Name | Marks |\n| --- | --- |\n| Asha | 90 |", encoding="utf-8")
    result = call_tool(ctx, "translate_text", {"source_path": "t.md", "target_language": "hi"})
    out = ctx.workspace.resolve_input(result["output_path"]).read_text(encoding="utf-8")
    assert result["checks"]["tables_rebuilt"] == 1
    assert [line.count("|") for line in out.strip().splitlines()] == [3, 3, 3] and "90" in out


def test_translate_segments_file_keeps_timings(ctx, inputs):
    import json

    doc = {"language": "hi", "segments": [{"start": 0.0, "end": 2.0, "text": "पहला"}, {"start": 2.5, "end": 5.0, "text": "दूसरा"}]}
    (inputs / "x.segments.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    result = call_tool(ctx, "translate_text", {"source_path": "x.segments.json", "target_language": "en"})
    out = load(ctx.workspace.resolve_input(result["output_path"]))
    assert [(s["start"], s["end"]) for s in out["segments"]] == [(0.0, 2.0), (2.5, 5.0)] and out["language"] == "en"


def test_translate_argument_errors(ctx):
    assert call_tool(ctx, "translate_text", {"target_language": "hi"})["error"]["code"] == "invalid_arguments"
    assert call_tool(ctx, "translate_text", {"text": "hi", "target_language": "fr"})["error"]["code"] == "unsupported_language"
    assert call_tool(ctx, "translate_text", {"text": "hi", "target_language": "hi", "extra": 1})["error"]["code"] == "invalid_arguments"
    assert call_tool(ctx, "translate_text", {"source_path": "missing.txt", "target_language": "hi"})["error"]["code"] == "file_not_found"


# ---- synthesize_speech -------------------------------------------------------


@needs_ffmpeg
def test_speech_long_text_is_split_into_short_requests_and_verified(ctx, fake):
    text = "यह पहला वाक्य है। " * 40
    result = call_tool(ctx, "synthesize_speech", {"text": text, "language": "hi", "gender": "male"})
    assert result["ok"] and result["voice"] == "Amit" and result["verification"]["passed"]
    sent = [kw["json"]["input"] for path, kw in fake.calls if path == "/v1/audio/speech"]
    assert len(sent) >= 3 and all(len(t) <= config.MAX_TTS_CHARS for t in sent)
    assert ctx.workspace.resolve_input(result["audio_path"]).stat().st_size > 1000
    assert result["verification"]["max_cer"] == 0.0


@needs_ffmpeg
def test_speech_repetition_is_detected_and_recovered(ctx, fake):
    text = "यह पहला वाक्य है। यह दूसरा वाक्य है।"
    fake.speak_glitch[text] = "repeat"  # the model says the text three times on the first try
    result = call_tool(ctx, "synthesize_speech", {"text": text, "language": "hi", "format": "wav"})
    assert result["ok"] and result["verification"]["passed"] and result["verification"]["retried_chunks"] == 2
    assert fake.count("/v1/audio/speech") == 3  # one bad attempt, then the two halves


@needs_ffmpeg
def test_speech_omission_is_detected_and_recovered(ctx, fake):
    text = "यह एक ही लंबा वाक्य है जिसमें कोई विराम नहीं है"
    fake.speak_glitch[text] = "omit"
    result = call_tool(ctx, "synthesize_speech", {"text": text, "language": "hi", "format": "wav"})
    assert result["ok"] and result["verification"]["passed"] and result["verification"]["retried_chunks"] >= 1


def test_speech_rejects_text_in_the_wrong_script(ctx, fake):
    error = call_tool(ctx, "synthesize_speech", {"text": "This is English text.", "language": "ta"})["error"]
    assert error["code"] == "invalid_arguments" and "translate_text" in error["hint"]
    assert fake.count("/v1/audio/speech") == 0


def test_speech_rejects_unknown_voice_and_style(ctx):
    assert "Ishita" in call_tool(ctx, "synthesize_speech", {"text": "নমস্কার", "language": "bn", "voice": "Bob"})["error"]["hint"]
    assert call_tool(ctx, "synthesize_speech", {"text": "নমস্কার", "language": "bn", "style": "pirate"})["error"]["code"] == "invalid_arguments"


# ---- ocr_document ------------------------------------------------------------


def _pdf(path, pages):
    from PIL import Image

    images = [Image.new("RGB", (300, 400), (255 - 50 * i, 255, 255)) for i in range(pages)]  # each page differs, so each is a new input
    images[0].save(path, save_all=True, append_images=images[1:])
    return path


def test_ocr_reads_a_pdf_page_by_page(ctx, fake, inputs):
    _pdf(inputs / "chapter.pdf", 3)
    fake.ocr_pages = ["# पाठ एक", "दूसरा पृष्ठ", "| क | ख |\n| --- | --- |\n| 1 | 2 |"]
    ctx.client.settings.max_parallel = 1
    result = call_tool(ctx, "ocr_document", {"path": "chapter.pdf"})
    assert result["ok"] and result["pages_read"] == 3 and fake.count("/v1/chat/completions") == 3
    text = ctx.workspace.resolve_input(result["markdown_path"]).read_text(encoding="utf-8")
    assert text.index("<!-- page 1 -->") < text.index("<!-- page 2 -->") < text.index("<!-- page 3 -->")
    assert abs(ctx.client.ledger.spent_inr - 3 * config.PRICE_OCR_PER_IMAGE) < 1e-6


def test_ocr_page_range_and_bad_ranges(ctx, fake, inputs):
    _pdf(inputs / "c.pdf", 4)
    assert call_tool(ctx, "ocr_document", {"path": "c.pdf", "pages": "2-3"})["pages_read"] == 2
    assert call_tool(ctx, "ocr_document", {"path": "c.pdf", "pages": "9"})["error"]["code"] == "invalid_arguments"
    assert call_tool(ctx, "ocr_document", {"path": "c.pdf", "pages": "x"})["error"]["code"] == "invalid_arguments"


# ---- files, safety and plumbing ---------------------------------------------


def test_paths_outside_the_workspace_are_refused(ctx, inputs, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    assert call_tool(ctx, "read_text", {"path": str(secret)})["error"]["code"] == "path_not_allowed"
    assert call_tool(ctx, "read_text", {"path": "../secret.txt"})["error"]["code"] in ("path_not_allowed", "file_not_found")


def test_read_text_pages_through_a_file(ctx, inputs):
    (inputs / "long.txt").write_text("a" * 5000, encoding="utf-8")
    first = call_tool(ctx, "read_text", {"path": "long.txt", "max_chars": 2000})
    assert len(first["untrusted_content"]) == 2000 and first["next_offset"] == 2000
    last = call_tool(ctx, "read_text", {"path": "long.txt", "offset": 4000, "max_chars": 2000})
    assert len(last["untrusted_content"]) == 1000 and last["next_offset"] is None


def test_write_text_refuses_indian_language_text(ctx):
    error = call_tool(ctx, "write_text", {"filename": "s.txt", "content": "சுருக்கம்: இது ஒரு பாடம்"})["error"]
    assert error["code"] == "content_rule_violation"
    assert call_tool(ctx, "write_text", {"filename": "../../s.txt", "content": "English summary."})["path"] == "artifacts/s.txt"


def test_subtitles_from_segments(ctx, inputs):
    import json

    doc = {"language": "en", "segments": [{"start": 0.0, "end": 2.0, "text": "Hello."}, {"start": 2.5, "end": 5.0, "text": "Welcome."}]}
    (inputs / "x.segments.json").write_text(json.dumps(doc), encoding="utf-8")
    result = call_tool(ctx, "make_subtitles", {"segments_path": "x.segments.json"})
    assert result["ok"] and result["cues"] == 2 and result["timestamps_in_order"]
    (inputs / "plain.txt").write_text("not segments", encoding="utf-8")
    assert call_tool(ctx, "make_subtitles", {"segments_path": "plain.txt"})["error"]["code"] == "invalid_arguments"


def test_same_input_is_never_sent_twice(ctx, fake):
    first = call_tool(ctx, "translate_text", {"text": "Good morning", "target_language": "hi"})
    spent = ctx.client.ledger.spent_inr
    second = call_tool(ctx, "translate_text", {"text": "Good morning", "target_language": "hi"})
    assert first["ok"] and second["ok"] and fake.count("/translate") == 1
    assert ctx.client.ledger.spent_inr == spent and ctx.client.ledger.cache_hits == 1


def test_budget_stops_a_job_before_it_spends(ctx, fake):
    ctx.client.ledger.max_cost_inr = 0.001
    error = call_tool(ctx, "synthesize_speech", {"text": "यह एक वाक्य है। " * 30, "language": "hi"})["error"]
    assert error["code"] == "budget_exceeded" and fake.count("/v1/audio/speech") == 0


def test_rate_limit_is_retried_then_succeeds(ctx, fake):
    fake.fail_next = [FakeResponse(429, {"error": {"message": "slow down", "code": "rate_limited", "request_id": "r"}}), FakeResponse(503)]
    assert call_tool(ctx, "translate_text", {"text": "Good morning", "target_language": "hi"})["ok"]
    assert fake.count("/translate") == 3


def test_persistent_failure_returns_a_structured_retryable_error(ctx, fake):
    fake.fail_next = [FakeResponse(500)] * 10
    error = call_tool(ctx, "translate_text", {"text": "Good morning", "target_language": "hi"})["error"]
    assert error["code"] == "upstream_error" and error["retryable"] is True


def test_client_error_is_not_retried(ctx, fake):
    fake.fail_next = [FakeResponse(400, {"error": {"message": "bad input", "code": "invalid_request", "request_id": "r9"}})]
    error = call_tool(ctx, "translate_text", {"text": "Good morning", "target_language": "hi"})["error"]
    assert error["code"] == "upstream_error" and "r9" in error["message"] and fake.count("/translate") == 1


def test_missing_key_gives_a_clear_error(ctx):
    ctx.client.settings.keys["translate"] = ""
    error = call_tool(ctx, "translate_text", {"text": "Good morning", "target_language": "hi"})["error"]
    assert error["code"] == "missing_api_key" and "BODHAN_TRANSLATE_KEY" in error["hint"]


def test_unknown_tool_and_tool_bug_never_raise(ctx, monkeypatch):
    assert call_tool(ctx, "launch_rocket", {})["error"]["code"] == "unknown_tool"
    monkeypatch.setattr("bodhan_mcp.langs.detect_script", lambda text: 1 / 0)
    assert call_tool(ctx, "translate_text", {"text": "Good morning", "target_language": "hi"})["error"]["code"] == "internal_error"


def test_inspect_lists_files_with_kinds(ctx, inputs):
    write_wav(inputs / "a.wav", tone(2))
    (inputs / "b.txt").write_text("नमस्ते", encoding="utf-8")
    _pdf(inputs / "c.pdf", 2)
    files = {f["path"].split("/")[-1]: f for f in call_tool(ctx, "inspect_files", {})["files"]}
    assert files["a.wav"]["kind"] == "audio" and files["b.txt"]["script"] == "Devanagari" and files["c.pdf"]["pages"] == 2


def test_cache_write_survives_a_locked_target(tmp_path, monkeypatch):
    """On Windows, replacing a file another thread holds raises PermissionError. The cache must cope."""
    from pathlib import Path

    from bodhan_mcp.client import DiskCache

    cache = DiskCache(tmp_path)
    cache.put("ab" + "0" * 62, b"first")
    monkeypatch.setattr(Path, "replace", lambda self, target: (_ for _ in ()).throw(PermissionError("locked")))
    cache.put("ab" + "0" * 62, b"first")  # same key, target exists: kept, no error
    cache.put("cd" + "0" * 62, b"new")  # target missing and replace keeps failing: skipped, no error
    assert cache.get("ab" + "0" * 62) == b"first" and cache.get("cd" + "0" * 62) is None
    assert not list(tmp_path.rglob("*.tmp*"))  # no temporary files left behind


def test_model_that_gives_up_on_an_input_is_not_retried():
    from bodhan_mcp.client import _model_gave_up

    assert _model_gave_up("502: the recognizer did not return a usable transcription for 1 of 6 blocks; the page was not transcribed")
    assert not _model_gave_up("502 Bad Gateway")


def test_hidden_files_such_as_env_are_never_read(ctx, inputs):
    (inputs / ".env").write_text("BODHAN_OCR_KEY=secret-value", encoding="utf-8")
    (inputs / "__pycache__").mkdir()
    (inputs / "__pycache__" / "x.pyc").write_bytes(b"0")
    (inputs / "note.txt").write_text("hello", encoding="utf-8")
    result = call_tool(ctx, "read_text", {"path": ".env"})
    assert not result["ok"] and result["error"]["code"] == "path_not_allowed" and "secret" not in str(result)
    listed = call_tool(ctx, "inspect_files", {})
    assert listed["ok"] and [f["path"] for f in listed["files"]] == ["note.txt"]
