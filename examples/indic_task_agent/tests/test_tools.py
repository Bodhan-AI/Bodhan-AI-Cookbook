"""Tools with a fake Bodhan client (no network, no spend) and real ffmpeg on generated media."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from bodhan_mcp import media, tools
from bodhan_mcp.bodhan import BodhanError


class FakeBodhan:
    def __init__(self, mp3: bytes = b""):
        self.calls, self.mp3 = [], mp3

    async def transcribe(self, audio, filename, duration_s, language=None):
        self.calls.append(("transcribe", round(duration_s, 1)))
        return "नमस्ते दुनिया"

    async def translate(self, text, target):
        self.calls.append(("translate", text))
        return text.replace("Hello", "नमस्ते")

    async def speak(self, text, voice, lang, style=None):
        self.calls.append(("speak", text))
        return self.mp3

    async def ocr(self, image, filename, table_format="markdown"):
        self.calls.append(("ocr", filename))
        return "| a | b |\n|---|---|\n| 1 | 2 |"


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture
def fake(tmp_path):
    mp3 = tmp_path / "beep.mp3"
    ffmpeg("-f", "lavfi", "-i", "sine=f=440:d=0.3", str(mp3))
    f = FakeBodhan(mp3.read_bytes())
    tools.configure(f, tmp_path / "out")
    return f


def run(coro):
    return asyncio.run(coro)


# --- pure helpers -------------------------------------------------------------------


def test_plan_chunks_without_silence_hard_cuts_at_30s():
    assert media.plan_chunks(75, []) == [(0, 30), (30, 60), (60, 75)]


def test_plan_chunks_cuts_in_silence_after_target():
    quiet = [(5.0, 5.6), (12.0, 13.0), (25.0, 26.0)]
    chunks = media.plan_chunks(40, quiet)
    assert chunks[0] == (0, 12.5)  # first silence after 10 s, cut at its middle
    assert all(e - s <= 30 for s, e in chunks) and chunks[-1][1] == 40


def test_trim_silence():
    assert media.trim_silence(0, 30, [(0, 3), (20, 35)]) == (2.8, 20.2)
    assert media.trim_silence(5, 8, [(4, 9)])[1] - media.trim_silence(5, 8, [(4, 9)])[0] <= 0.4  # all silence


def test_speech_seconds():
    assert media.speech_seconds(0, 10, [(2, 4), (8, 20)]) == 6


def test_split_markdown_keeps_tables_and_code_whole():
    table = "| Q | A |\n|---|---|\n" + "".join(f"| {i} | answer {i} |\n" for i in range(40))
    text = f"Intro paragraph.\n\n{table}\n```\ncode line\n\nmore code\n```\n\n" + "Long sentence here. " * 40
    chunks = tools.split_markdown(text, 300)
    assert table.strip() in chunks  # table intact even though it is longer than 300
    assert "```\ncode line\n\nmore code\n```" in chunks
    assert all(len(c) <= 300 for c in chunks if not c.startswith(("|", "```")))
    assert "".join(chunks).replace("\n", "").replace(" ", "") == text.replace("\n", "").replace(" ", "")


def test_split_sentences_hindi_danda():
    pieces = tools.split_sentences("पहला वाक्य। दूसरा वाक्य। " * 20, 60)
    assert all(len(p) <= 60 for p in pieces) and all(p.endswith("।") for p in pieces)


def test_srt_timestamps_and_cues():
    assert tools._ts(3725.5) == "01:02:05,500"
    cues = tools._cues(0, 10, "word " * 40, max_chars=84)
    assert len(cues) == 3 and cues[0][0] == 0 and abs(cues[-1][1] - 10) < 1e-9
    lengths = [len(c[2]) for c in tools._cues(0, 6, "Hello, today we will start our math class. Please open your books and read the first page.")]
    assert len(lengths) == 2 and min(lengths) > 30  # balanced, no one-word tail cue


# --- tools ------------------------------------------------------------------------------


def test_transcribe_splits_on_silence_and_skips_silent_windows(fake, tmp_path):
    clip = tmp_path / "talk.wav"  # 12 s tone, 2 s silence, 12 s tone, 15 s silence
    ffmpeg("-f", "lavfi", "-i", "sine=f=300:d=12", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=2",
           "-f", "lavfi", "-i", "sine=f=300:d=12", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:d=15",
           "-filter_complex", "[0][1][2][3]concat=n=4:v=0:a=1", "-ar", "16000", "-ac", "1", str(clip))
    r = run(tools.transcribe_audio(str(clip)))
    durations = [d for name, d in fake.calls if name == "transcribe"]
    assert len(durations) == 2 and all(d <= 13.5 for d in durations)  # silent edges trimmed, not billed
    assert r["silence_skipped_s"] > 14 and r["language"] == "hi"
    seg = json.loads(Path(r["segments_path"]).read_text())
    assert seg["segments"][0]["start"] == 0 and len(seg["segments"]) == 2


def test_translate_text_writes_file_and_keeps_table(fake):
    r = run(tools.translate_text("Marathi", text="Hello class.\n\n| a | b |\n|---|---|\n| 1 | 2 |"))
    assert r["target_language"] == "mr" and r["warnings"] == []
    assert Path(r["output_path"]).read_text().startswith("नमस्ते class.")


def test_synthesize_speech_concats_and_labels(fake):
    r = run(tools.synthesize_speech("hi", text="पहला वाक्य। " * 40))
    assert r["pieces"] > 1 and r["voice"] == "Kavya" and r["duration_s"] > 0.5
    tags = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=comment", "-of", "csv=p=0",
                           r["output_path"]], capture_output=True, text=True).stdout
    assert "AI-generated" in tags


def test_synthesize_rejects_text_in_wrong_language(fake):
    with pytest.raises(BodhanError) as e:
        run(tools.synthesize_speech("ta", text="This is English."))
    assert e.value.code == "wrong_script" and fake.calls == []


def test_ocr_pdf_one_call_per_page(fake, tmp_path):
    pdf = tmp_path / "scan.pdf"
    pages = [Image.new("RGB", (200, 100), "white") for _ in range(3)]
    pages[0].save(pdf, save_all=True, append_images=pages[1:])
    r = run(tools.ocr_document(str(pdf)))
    assert r["pages"] == 3 and len(fake.calls) == 3
    assert Path(r["output_path"]).read_text().count("<!-- page") == 3


def test_make_subtitles_translates_segments(fake, tmp_path):
    seg = tmp_path / "note.segments.json"
    seg.write_text(json.dumps({"language": "hi", "segments": [{"start": 0, "end": 4, "text": "Hello"}]}))
    r = run(tools.make_subtitles(str(seg), "en"))
    assert Path(r["output_path"]).read_text() == "1\n00:00:00,000 --> 00:00:04,000\nनमस्ते\n"


@pytest.mark.parametrize("call,code", [
    (lambda: tools.transcribe_audio("/no/such.wav"), "file_not_found"),
    (lambda: tools.translate_text("French", text="hello"), "unsupported_language"),
    (lambda: tools.translate_text("hi", text="a", path="b"), "bad_request"),
    (lambda: tools.synthesize_speech("hi", text="क" * 6000), "too_long"),
])
def test_bad_input_errors(fake, call, code):
    with pytest.raises(BodhanError) as e:
        run(call())
    assert e.value.code == code and fake.calls == []


# --- the real server over stdio ------------------------------------------------------------


def test_mcp_server_over_stdio(tmp_path):
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def go():
        params = StdioServerParameters(command=sys.executable, args=["-m", "bodhan_mcp", "--out-dir", str(tmp_path)],
                                       cwd=str(Path(__file__).parents[1]))
        async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            bad = await s.call_tool("transcribe_audio", {"path": "/no/such.wav"})
            voices = await s.read_resource("bodhan://voices")
            return names, bad, voices

    names, bad, voices = run(go())
    assert {"transcribe_audio", "translate_text", "synthesize_speech", "ocr_document"} <= names
    text = bad.content[0].text  # "Error executing tool transcribe_audio: {json}"
    assert bad.is_error and json.loads(text[text.index("{"):])["code"] == "file_not_found"
    assert "Kavya" in voices.contents[0].text
