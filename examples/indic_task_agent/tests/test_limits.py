"""Chunking and limit handling for each tool, against the fake backend."""

import asyncio
import json

import pymupdf
from mcp import Client

from bodhan_mcp import media, server
from bodhan_mcp import text as tx
from fakes import wav_seconds


def call(name, args):
    async def go():
        async with Client(server.server) as c:
            r = await c.call_tool(name, args)
            return r.is_error, (r.structured_content if not r.is_error else json.loads(r.content[0].text.split(": ", 1)[1]))

    return asyncio.run(go())


# ---------------------------------------------------------------- audio windows


def test_plan_cuts_cuts_at_pauses_and_respects_limit():
    silent = [(10, 13), (23, 26), (36, 39), (49, 52), (62, 65)]
    windows = media.plan_cuts(70, silent)
    assert windows[0] == (0.0, 24.5) and windows[-1][1] == 70
    assert all(b - a <= media.MAX_WINDOW_S for a, b in windows)
    assert all(a == prev[1] for prev, (a, _) in zip(windows, windows[1:]))  # contiguous, nothing dropped


def test_plan_cuts_hard_cut_without_pauses():
    windows = media.plan_cuts(65, [])
    assert [round(b - a) for a, b in windows] == [28, 28, 9]


def test_transcribe_sends_only_windows_under_30s(fake, speech_wav):
    sent = []
    fake.asr = lambda audio, lang: sent.append(wav_seconds(audio)) or "नमस्ते दोस्तों"
    err, out = call("transcribe_audio", {"path": str(speech_wav)})
    assert not err, out
    assert len(sent) == 3 and max(sent) <= 30
    assert out["language"]["language"] == "hi" and out["segments"] == 3
    assert any("detected 'hi'" in n for n in out["notes"])
    # The language detected on chunk 1 is named for the rest.
    langs = [c[1].get("language") for c in fake.calls]
    assert langs == [None, "hi", "hi"]


def test_cache_means_no_second_paid_call(fake, speech_wav):
    call("transcribe_audio", {"path": str(speech_wav), "language": "hi"})
    n = len(fake.calls)
    err, out = call("transcribe_audio", {"path": str(speech_wav), "language": "hi"})
    assert len(fake.calls) == n and out["cost_inr"] == 0 and out["cached_calls"] == 3


def test_budget_refuses_before_spending(fake, speech_wav):
    err, out = call("transcribe_audio", {"path": str(speech_wav), "max_cost_inr": 0.01})
    assert err and out["error"]["code"] == "budget_exceeded" and fake.calls == []


# ---------------------------------------------------------------- speech


def test_split_for_speech_keeps_text_and_limit():
    text = "पहला वाक्य है। " * 40 + "a" * 500
    chunks = tx.split_for_speech(text, 220)
    assert all(len(c) <= 220 for c in chunks)
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")


def test_speech_chunks_joins_and_verifies(fake, env):
    src = env / "hi.txt"
    src.write_text("यह पहला वाक्य है। " * 30, encoding="utf-8")
    fake.asr = lambda audio, lang: "यह पहला वाक्य है। " * 20  # wrong length -> some chunks fail verification
    err, out = call("synthesize_speech", {"language": "hi", "path": str(src), "output_path": str(env / "o.mp3")})
    assert not err, out
    assert out["chunks"] > 1 and out["ai_generated"] and (env / "o.mp3").stat().st_size > 0
    speak = [c for c in fake.calls if c[0] == "/v1/audio/speech"]
    assert all(len(c[1]["input"]) <= 220 for c in speak)
    assert out["voice"] == "Kavya" and out["verification"]["method"].startswith("back-transcription")


def test_speech_rejects_wrong_language(fake, env):
    err, out = call("synthesize_speech", {"language": "ta", "text": "This is English."})
    assert err and out["error"]["code"] == "language_mismatch" and fake.calls == []


# ---------------------------------------------------------------- translation


def test_markdown_table_survives_translation(fake, env):
    md = "# Worksheet\n\n| Name | Marks |\n|---|---|\n| Ram | 10 |\n| Sita | 12 |\n\n- First question here"
    src = env / "w.md"
    src.write_text(md)
    err, out = call("translate_text", {"path": str(src), "target_language": "mr"})
    assert not err, out
    result = open(out["output_path"]).read().split("\n")
    assert result[3] == "|---|---|" and result[0].startswith("# ") and result[-1].startswith("- ")
    assert all(line.count("|") == 3 for line in result[2:6])
    assert "10" in result[4]  # numbers are not sent for translation
    assert out["verification"]["passed"]


def test_truncated_translation_is_split_and_redone(fake, env):
    fake.truncate_over = 300
    text = " ".join(f"This is sentence number {i}." for i in range(60))
    err, out = call("translate_text", {"text": text, "target_language": "hi"})
    assert not err, out
    assert out["verification"]["passed"]
    assert all(len(c[1]["messages"][0]["content"]) <= 300 for c in fake.calls[-4:])


def test_wrapped_lines_become_one_paragraph_unit():
    lines, units = tx.md_template("The water cycle moves water\nbetween the sea and the air.\n\n- a list item\n- another")
    assert units == ["The water cycle moves water between the sea and the air.", "a list item", "another"]
    assert tx.md_fill(lines, ["P", "A", "B"]) == "P\n\n- A\n- B"


def test_translation_units_fit_context():
    lines, units = tx.md_template("word " * 2000, max_chars=1500)
    assert len(units) >= 7 and all(len(u) <= 1500 for u in units)


# ---------------------------------------------------------------- OCR


def test_multipage_pdf_is_read_page_by_page(fake, env):
    pdf = env / "doc.pdf"
    doc = pymupdf.open()
    for i in range(3):
        doc.new_page().insert_text((72, 72), f"Page {i + 1}")
    doc.save(pdf)
    err, out = call("ocr_document", {"path": str(pdf)})
    assert not err, out
    assert out["pages_done"] == [1, 2, 3] and len(fake.calls) == 3
    assert abs(out["cost_inr"] - 0.6) < 1e-9 and out["language"]["script"] == "Devanagari"
    err, out = call("ocr_document", {"path": str(pdf), "pages": "2-9"})
    assert err and out["error"]["code"] == "invalid_pages"


# ---------------------------------------------------------------- language ID and bad inputs


def test_detect_language_by_script():
    assert tx.detect_language("வணக்கம் நண்பர்களே")["language"] == "ta"
    assert tx.detect_language("నమస్కారం")["language"] == "te"
    assert tx.detect_language("मी घरी आहे")["language"] == "mr"
    assert tx.detect_language("This is the English text.")["language"] == "en"
    assert tx.detect_language("Worksheet Food Nutrient Rice Carbohydrates Lentils Protein")["language"] == "en"
    french = "Bonjour mes amis, je suis très heureux de vous voir ici aujourd'hui. " * 4
    assert tx.detect_language(french)["language"] is None


def test_bad_inputs_return_structured_errors(env):
    assert call("inspect_media", {"path": "/no/such.wav"})[1]["error"]["code"] == "file_not_found"
    assert call("transcribe_audio", {"path": str(env)})[1]["error"]["code"] == "is_a_directory"
    assert call("translate_text", {"target_language": "hi"})[1]["error"]["code"] == "invalid_request"
    err, _ = asyncio.run(_raw("translate_text", {"target_language": "fr", "text": "hello"}))
    assert err


async def _raw(name, args):
    async with Client(server.server) as c:
        r = await c.call_tool(name, args)
        return r.is_error, r.content[0].text
