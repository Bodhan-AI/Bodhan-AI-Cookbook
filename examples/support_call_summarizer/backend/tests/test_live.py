"""Optional end-to-end test against the real Bodhan API.

Runs only when Bodhan keys are set in the environment or .env (skipped in CI):

    pytest -m live
"""

import os
from pathlib import Path

import pytest

from app.bodhan_client import BodhanClient
from app.config import RECIPE_ROOT, Settings
from app.models import CallStatus
from app.pipeline import CallInput, process_call
from tests.conftest import needs_ffmpeg

pytestmark = [
    pytest.mark.live,
    needs_ffmpeg,
    pytest.mark.skipif(
        not (os.environ.get("RUN_LIVE") and Settings().transcribe_key and Settings().translate_key),
        reason="set RUN_LIVE=1 and the Bodhan keys to call the real API",
    ),
]

CANDIDATES = [
    RECIPE_ROOT / "sample_data" / "sample_support_call_hindi.wav",  # made by scripts/make_sample_call.py
    RECIPE_ROOT.parents[1] / "sample_data" / "sample_hindi.wav",  # shared clip in the cookbook repo
]


def sample() -> Path:
    for path in CANDIDATES:
        if path.exists():
            return path
    pytest.skip("no sample recording found; run scripts/make_sample_call.py")


async def test_real_call_end_to_end(tmp_path):
    settings = Settings()
    client = BodhanClient(settings.transcribe_key, settings.translate_key)
    path = sample()
    try:
        record = await process_call(
            CallInput(call_id="live", audio_path=path, source_file=path.name, language="hi"),
            client=client,
            settings=settings,
            workdir=tmp_path,
        )
    finally:
        await client.aclose()
    assert record.status == CallStatus.completed, record.warnings
    assert record.segments and all(s.original_text for s in record.segments)
    assert all(s.english_text for s in record.segments)
