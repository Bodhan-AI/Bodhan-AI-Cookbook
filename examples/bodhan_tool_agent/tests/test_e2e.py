"""
End-to-end smoke test: runs a multi-step task (transcribe + translate)
against the real Bodhan API.

Requires a valid .env file with BODHAN_* and LLM_* keys.
Run with:  pytest tests/test_e2e.py -v -s

Skipped automatically if BODHAN_TRANSCRIBE_KEY is not set.
"""
from __future__ import annotations

import os
import struct
import wave
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv()

pytestmark = pytest.mark.skipif(
    not os.getenv("BODHAN_TRANSCRIBE_KEY"),
    reason="BODHAN_TRANSCRIBE_KEY not set - skipping live API test",
)


def _make_silent_wav(path: Path, duration_s: float = 5.0) -> Path:
    """Create a minimal silent WAV (real audio would be needed for a real transcript)."""
    n = int(duration_s * 16000)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(struct.pack(f"<{n}h", *([0] * n)))
    return path


@pytest.mark.asyncio
async def test_transcribe_then_translate(tmp_path: Path) -> None:
    """Transcribe a WAV file then translate the result."""
    from dotenv import load_dotenv
    load_dotenv()

    from bodhan_mcp.tools.transcribe import transcribe_audio
    from bodhan_mcp.tools.translate import translate_text

    wav = _make_silent_wav(tmp_path / "test.wav")

    # Transcribe (silent file returns empty or near-empty transcript)
    t_result = await transcribe_audio(wav)
    assert "text" in t_result
    assert "cost_inr" in t_result

    # Translate whatever came back (even empty string)
    tr_result = await translate_text(t_result["text"] or "Hello.", "en", "hi")
    assert "translated_text" in tr_result
    assert tr_result["source_lang"] == "en"
    assert tr_result["target_lang"] == "hi"


@pytest.mark.asyncio
async def test_full_agent_run(tmp_path: Path) -> None:
    """Run the agent on a minimal task and confirm output files are written."""
    from dotenv import load_dotenv
    load_dotenv()

    from bodhan_agent.agent import run

    # Use a plain-text file so the agent doesn't need audio or OCR
    txt = tmp_path / "input.txt"
    txt.write_text("The sun rises in the east.", encoding="utf-8")

    out_dir = tmp_path / "out"
    await run(
        task="Translate this text into Hindi.",
        files=[txt],
        out_dir=out_dir,
        max_steps=10,
        max_cost_inr=5.0,
    )

    assert (out_dir / "result.md").exists()
    assert (out_dir / "report.json").exists()
    assert (out_dir / "trace.jsonl").exists()
