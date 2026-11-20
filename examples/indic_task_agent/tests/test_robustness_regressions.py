"""Robustness cases: middle pages of a long scan, a corrupted audio tail, and a hard process kill."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import make_pdf
from fakes import FakeBodhan
from test_agent_e2e import read_outputs

from bodhan_mcp.registry import invoke
from bodhan_mcp.tools import TOOL_BY_NAME

KEYS = ("BODHAN_TRANSCRIBE_KEY", "BODHAN_TRANSLATE_KEY", "BODHAN_SPEAK_KEY", "BODHAN_OCR_KEY")


@pytest.fixture(autouse=True)
def dummy_keys(monkeypatch):
    for k in KEYS:
        monkeypatch.setenv(k, "test-key")


async def call(ctx, tool_name, **a):
    return await invoke(TOOL_BY_NAME[tool_name], ctx, a)


# 1. a fact on page 5 of 8 reaches the English text the planner reads: no head/tail previewing of long documents
async def test_middle_page_fact_reaches_the_english_text(make_ctx, tmp_path):
    pages = [f"பக்கம் {n} பாடம் நீர் சுழற்சி விளக்கம்" for n in range(1, 9)]
    pages[4] = "பள்ளி ஆண்டு விழா 1847 மாணவர்கள் பங்கேற்பு"
    ctx = make_ctx(FakeBodhan(ocr_pages=pages))
    r, err = await call(ctx, "ocr_document", path=str(make_pdf(tmp_path / "chapter.pdf", 8)))
    assert not err and r["pages_ocr"] == 8 and not r["incomplete_pages"]
    t, err = await call(ctx, "translate_text", path=r["path"], target_language="en")
    assert not err
    english = Path(t["path"]).read_text(encoding="utf-8")
    assert "1847" in english  # the middle page was translated, not skipped
    seen, offset = "", 0
    while offset is not None:  # paging through read_text returns every character
        page, err = await call(ctx, "read_text", path=t["path"], offset=offset, max_chars=200)
        assert not err
        seen, offset = seen + page["text"], page["next_offset"]
    assert seen.replace("\r\n", "\n") == english  # same text; Windows writes CRLF


# 2. only the last segment of a long speech is wrong: the result must not be reported as verified
async def test_corrupted_final_segment_is_not_verified(make_ctx):
    body = "यह पाठ का एक सामान्य वाक्य है जो सही बोला जाता है। " * 12
    tail = "अंतिम भाग में नदी समुद्र तक पहुँचती है और चक्र पूरा होता है।"
    fake = FakeBodhan(speak_garble_if="समुद्र")  # every input containing the tail's word, including repairs, comes back wrong
    r, err = await call(make_ctx(fake), "synthesize_speech", text=body + tail, language="hi", format="wav")
    assert not err and r["segments"] >= 3
    assert r["verification"]["status"] == "fail", r["verification"]
    weak = [s["index"] for s in r["segment_report"] if s["similarity"] < 0.5]
    assert weak == [r["segments"] - 1]  # found in the final segment, after clean earlier ones


# 3. a run killed without any cleanup resumes from its checkpoint and does not pay again for finished work
def test_resume_after_a_hard_process_kill(tmp_path):
    (tmp_path / "p.txt").write_text("The library opens at eight o'clock every morning.", encoding="utf-8")
    child = [sys.executable, str(Path(__file__).with_name("_kill_resume_child.py"))]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", **{k: "test-key" for k in KEYS}}
    checkpoint = tmp_path / "out" / ".state" / "checkpoint.json"
    proc = subprocess.Popen([*child, "first", str(tmp_path)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 180  # generous: a loaded CI machine can be slow to start Python
        while time.monotonic() < deadline:
            try:
                state = json.loads(checkpoint.read_text(encoding="utf-8"))
                if any(t.get("tool") == "translate_text" for t in state.get("tool_log", [])):
                    break
            except (OSError, ValueError):
                pass
            assert proc.poll() is None, proc.communicate()
            time.sleep(0.1)
        else:
            pytest.fail("the first run never checkpointed its translation")
        time.sleep(0.3)  # let the child enter the hanging planner call
    finally:
        proc.kill()  # SIGKILL / TerminateProcess: nothing in the child gets to clean up
        proc.communicate()
    provisional = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))  # a killed run still has a report
    assert provisional["provisional"] and provisional["stop_reason"] == "interrupted"

    done = subprocess.run([*child, "resume", str(tmp_path)], env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stderr
    second_calls = json.loads(done.stdout.strip().splitlines()[-1])
    out, rep, trace, md = read_outputs(tmp_path)
    assert rep["status"] in ("completed", "completed_with_warnings")
    assert second_calls.get("translate", 0) == 0  # the translation paid for before the kill was not bought again
    assert any(e["type"] == "run_resumed" for e in trace)
    paid = [e for e in trace if e["type"] == "api_call" and e.get("model") == "indic-translate" and e.get("cache") == "miss"]
    assert paid and rep["cost"]["bodhan_billed_inr"] >= sum(e.get("cost_inr") or 0 for e in paid) - 1e-9


# ---- size limits: refused before any Bodhan request -------------------------------------------------


async def _invoke(ctx, tool, **a):
    from bodhan_mcp.registry import invoke
    from bodhan_mcp.tools import TOOL_BY_NAME

    return await invoke(TOOL_BY_NAME[tool], ctx, a)


async def test_a_text_file_over_5_mb_is_refused_unread(make_ctx, tmp_path):
    from fakes import FakeBodhan

    big = tmp_path / "big.txt"
    with big.open("wb") as f:
        f.truncate(5_000_001)
    fake = FakeBodhan()
    r, is_error = await _invoke(make_ctx(fake), "read_text", path=str(big))
    assert is_error and r["error"]["code"] == "invalid_argument" and "5 MB" in r["error"]["message"]


async def test_more_than_60_pdf_pages_in_one_call_is_refused(make_ctx, tmp_path):
    import pypdfium2 as pdfium
    from fakes import FakeBodhan

    pdf = pdfium.PdfDocument.new()
    for _ in range(61):
        pdf.new_page(200, 200)
    path = tmp_path / "long.pdf"
    pdf.save(str(path))
    fake = FakeBodhan()
    r, is_error = await _invoke(make_ctx(fake), "ocr_document", path=str(path))
    assert is_error and r["error"]["code"] == "invalid_argument" and "limit is 60" in r["error"]["message"]
    assert fake.requests == [] and sum(fake.calls.values()) == 0


async def test_audio_over_60_minutes_is_refused_before_decoding(make_ctx, tmp_path, monkeypatch):
    import numpy as np
    from conftest import write_wav
    from fakes import FakeBodhan

    from bodhan_mcp.media import MediaInfo
    from bodhan_mcp.tools import audio_tools

    wav = write_wav(tmp_path / "long.wav", np.zeros(16000, dtype=np.float32))
    monkeypatch.setattr(audio_tools, "probe", lambda p: MediaInfo(3601.0, True, False, "pcm_s16le", 16000, 1, "wav"))
    monkeypatch.setattr(audio_tools, "decode_pcm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("decoded")))
    fake = FakeBodhan()
    r, is_error = await _invoke(make_ctx(fake), "transcribe_audio", path=str(wav))
    assert is_error and r["error"]["code"] == "invalid_argument" and "limit is 60" in r["error"]["message"]
    assert sum(fake.calls.values()) == 0
