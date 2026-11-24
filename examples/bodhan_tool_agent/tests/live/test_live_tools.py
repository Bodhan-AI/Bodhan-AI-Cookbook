"""Live round trip through the real Bodhan API (≈₹0.05). Run with: pytest -m live tests/live

Uses the cookbook's 6 s Hindi sample. Needs BODHAN_*_KEY in the environment or .env.
"""

import json
from pathlib import Path

import pytest

from bodhan_core.config import load_settings
from bodhan_mcp import runtime, server

pytestmark = pytest.mark.live
SAMPLE = Path(__file__).resolve().parents[4] / "sample_data" / "sample_hindi.wav"


@pytest.fixture(autouse=True)
def live_client(tmp_path, monkeypatch):
    settings = load_settings()
    if len(settings.bodhan_keys) < 4:
        pytest.skip("Bodhan keys not configured")
    monkeypatch.setenv("BODHAN_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("BODHAN_MAX_COST_INR", "0.5")
    runtime.set_client(None)
    yield
    print(f"\nlive spend: ₹{runtime.get_client().ledger.spent_inr:.4f}")
    runtime.set_client(None)


async def test_live_transcribe_translate_speak_subtitles():
    t = await server.transcribe_audio(str(SAMPLE), segment_style="subtitle")
    assert t["ok"], t
    assert t["language"] == "hi" or "Devanagari" in str(t["script"])
    assert "गणित" in Path(t["transcript_path"]).read_text(encoding="utf-8")

    en = await server.translate_segments(t["segments_path"], "en")
    assert en["ok"] and en["coverage"]["complete"], en
    srt = await server.make_srt(en["segments_path"])
    assert srt["ok"] and srt["valid"]

    ta = await server.translate_text("ta", text="Please open your books.")
    assert ta["ok"] and ta["issue_count"] == 0, ta

    speech = await server.synthesize_speech("ta", text=ta["translation"], gender="female")
    assert speech["ok"], speech
    assert speech["verification"]["checked"]
    print(json.dumps({"transcript": t["preview"], "english": en["preview"], "tamil": ta["translation"],
                      "speech": {k: speech[k] for k in ("voice", "duration_s", "verification", "cost_inr")}},
                     ensure_ascii=False, indent=1))
