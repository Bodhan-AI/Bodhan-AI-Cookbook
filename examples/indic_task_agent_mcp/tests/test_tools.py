"""Each tool's chunking and limit handling, against the fake Bodhan API."""

import json
from pathlib import Path

import numpy as np
import pytest
from conftest import needs_ffmpeg, silence, to_wav, tone

from bodhan_mcp import tools
from bodhan_mcp.errors import ToolError


async def test_translate_keeps_table_code_and_numbers(ctx, fake_api, workdir):
    src = workdir / "worksheet.md"
    src.write_text("# Worksheet\n\nAnswer all 5 questions.\n\n| Q | Marks |\n| --- | --- |\n| Angles | 10 |\n\n"
                   "```\nprint(1)\n```\n\n$$\nx^2 + 2x = 0\n$$\n", encoding="utf-8")
    res = await tools.translate_text(ctx, "Marathi", path=str(src))
    out = Path(res.output_path).read_text(encoding="utf-8")
    assert res.target_language == "mr"
    assert res.missing_numbers == []
    assert "| --- | --- |" in out and "print(1)" in out and "x^2 + 2x = 0" in out
    assert res.passthrough_units == 2 and res.script_match > 0.9
    assert res.output_path.endswith(".md")


async def test_translate_rejects_unsupported_and_empty(ctx):
    with pytest.raises(ToolError) as e1:
        await tools.translate_text(ctx, "French", text="hello")
    assert e1.value.code == "unsupported_language" and "ta (Tamil)" in e1.value.hint
    with pytest.raises(ToolError) as e2:
        await tools.translate_text(ctx, "ta")
    assert e2.value.code == "invalid_argument"


@needs_ffmpeg
async def test_speech_is_chunked_verified_and_normalised(ctx, fake_api, workdir):
    text = "यह पहला वाक्य है। " * 30  # about 540 characters, so several chunks
    res = await tools.synthesize_speech(ctx, "hi", text=text, gender="female")
    assert res.chunks >= 3 and res.voice == "Kavya"
    assert res.verification.checked and res.verification.chunks_flagged == 0
    assert res.verification.max_cer == 0.0
    assert Path(res.audio_path).exists() and res.audio_path.endswith(".mp3")
    assert "AI-generated" in res.label
    details = json.loads(Path(res.details_path).read_text(encoding="utf-8"))
    assert len(details["chunks"]) == res.chunks


@needs_ffmpeg
async def test_speech_repetition_is_detected_and_recovered(ctx, fake_api):
    fake_api.speak_mode = "repeat"
    res = await tools.synthesize_speech(ctx, "hi", text="यह पहला वाक्य है, और यह दूसरा हिस्सा है। यह तीसरा वाक्य है।")
    assert res.verification.chunks_retried == 1
    assert res.verification.chunks_flagged == 1  # the fake keeps repeating, so it is reported, not hidden
    assert any("differ" in w for w in res.warnings)


async def test_speech_requires_target_script(ctx):
    with pytest.raises(ToolError) as exc:
        await tools.synthesize_speech(ctx, "ta", text="This is English text")
    assert exc.value.code == "invalid_argument" and "translate_text" in exc.value.hint


async def test_unknown_voice_lists_options(ctx):
    with pytest.raises(ToolError) as exc:
        await tools.synthesize_speech(ctx, "bn", text="আমি", voice="Morgan")
    assert exc.value.code == "unknown_voice" and "Ishita" in exc.value.hint


@needs_ffmpeg
async def test_transcribe_long_audio_in_windows_and_subtitles(ctx, fake_api, workdir):
    parts = []
    for _ in range(8):
        parts += [tone(6), silence(0.8)]
    path = workdir / "note.wav"
    path.write_bytes(to_wav(np.concatenate([silence(2), *parts])))
    res = await tools.transcribe_audio(ctx, str(path))
    assert res.language == "hi" and res.language_source == "detected_by_script"
    assert res.windows_sent >= 3
    assert fake_api.calls["transcribe"] == res.windows_sent
    segs = json.loads(Path(res.segments_path).read_text(encoding="utf-8"))["segments"]
    assert all(s["end"] - s["start"] <= 28.0 for s in segs)
    tr = await tools.translate_segments(ctx, res.segments_path, "en")
    sub = await tools.write_subtitles(ctx, tr.segments_path)
    srt = Path(sub.srt_path).read_text(encoding="utf-8")
    assert sub.cues >= res.windows_sent and "-->" in srt


async def test_ocr_pdf_and_split_retry_on_502(ctx, fake_api, workdir):
    from PIL import Image

    pdf = workdir / "chapter.pdf"
    pages = [Image.new("RGB", (800, 1100), "white") for _ in range(3)]
    pages[0].save(pdf, save_all=True, append_images=pages[1:])
    fake_api.ocr_fail_once = True
    res = await tools.ocr_document(ctx, str(pdf), pages="1-2")
    assert res.page_count == 2
    assert sum(p.split_retry for p in res.pages) == 1
    assert any("low-confidence" in f for f in res.flags)
    assert "<!-- page 1 -->" in Path(res.output_path).read_text(encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        await tools.ocr_document(ctx, str(pdf), pages="7")
    assert exc.value.code == "invalid_argument"


async def test_paths_outside_allowed_folders_are_refused(ctx):
    with pytest.raises(ToolError) as exc:
        await tools.read_text(ctx, "/etc/passwd")
    assert exc.value.code == "path_not_allowed"


async def test_inspect_folder_lists_files(ctx, workdir):
    (workdir / "a.txt").write_text("নমস্কার, এটা বাংলা।", encoding="utf-8")
    res = await tools.inspect_file(ctx, str(workdir))
    assert res.kind == "folder" and any(e.path.endswith("a.txt") for e in res.entries)
    one = await tools.inspect_file(ctx, str(workdir / "a.txt"))
    assert one.language_guess["language"] == "bn"
