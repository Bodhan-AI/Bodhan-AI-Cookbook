"""Live smoke test against the real Bodhan API (about ₹0.20). Skipped unless BODHAN_LIVE=1 and keys are set.

    BODHAN_LIVE=1 pytest -q tests/test_live_smoke.py -s

Checks the response shapes this recipe depends on: translation text, WAV speech, transcription text, OCR text.
"""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
pytestmark = [pytest.mark.live, pytest.mark.skipif(os.environ.get("BODHAN_LIVE") != "1", reason="set BODHAN_LIVE=1 to call the real API")]


async def test_each_model_once(tmp_path, monkeypatch):
    from PIL import Image, ImageDraw

    from bodhan_mcp import tools
    from bodhan_mcp.config import Settings

    monkeypatch.delenv("BODHAN_BASE_URL", raising=False)
    ctx = tools.ToolContext(Settings(output_dir=tmp_path / "out", allowed_dirs=[tmp_path], cache_dir=tmp_path / "cache", max_cost_inr=1.0))
    tr = await tools.translate_text(ctx, "hi", text="Water boils at 100 degrees Celsius.")
    print("translate:", tr.preview, tr.warnings)
    assert tr.script_match and tr.script_match > 0.6 and not tr.missing_numbers
    ta = await tools.translate_text(ctx, "ta", text="The library opens at 9 in the morning.")
    print("translate ta:", ta.preview, ta.warnings)
    assert ta.script_match and ta.script_match > 0.6, "target language was not honoured"
    sp = await tools.synthesize_speech(ctx, "hi", path=tr.output_path)
    print("speech:", sp.duration_s, "s, mean CER", sp.verification.mean_cer)
    assert sp.duration_s > 1 and sp.verification.checked
    img = Image.new("RGB", (900, 300), "white")
    ImageDraw.Draw(img).text((40, 120), "Chapter 6: Lines and Angles", fill="black")
    img.save(tmp_path / "page.png")
    oc = await tools.ocr_document(ctx, str(tmp_path / "page.png"))
    print("ocr:", oc.preview[:80], oc.flags)
    assert oc.page_count == 1
    print("total cost ₹", round(ctx.ledger.spent, 4))
    await ctx.client.aclose()
