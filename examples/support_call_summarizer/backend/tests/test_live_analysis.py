"""Optional test of the call summary against the real LLM APIs (Sarvam, then Claude as the fallback).

Runs only when SARVAM_API_KEY or ANTHROPIC_API_KEY is set in the environment or .env (skipped in CI):

    RUN_LIVE=1 pytest -m live tests/test_live_analysis.py
"""

import os

import pytest

from app.analysis import build_analyzer
from app.config import Settings
from app.models import Segment, Sentiment

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.environ.get("RUN_LIVE") and Settings().analysis_enabled),
        reason="set RUN_LIVE=1 and SARVAM_API_KEY or ANTHROPIC_API_KEY to call the real API",
    ),
]

CALL = [
    ("Speaker 1", "Thank you for calling. How can I help you?"),
    ("Speaker 2", "My internet has not worked for two days and nobody is helping me. This is ridiculous."),
    ("Speaker 1", "I am sorry about that. Let me check your connection. I see an outage in your area."),
    ("Speaker 2", "When will it be fixed? I work from home."),
    ("Speaker 1", "The team expects it to be fixed by tonight. I will call you tomorrow morning to confirm."),
    ("Speaker 2", "Okay, thank you for explaining. That helps."),
]


async def test_real_summary_and_sentiment():
    segments = [
        Segment(segment_no=i + 1, start=i * 6, end=i * 6 + 6, speaker=who, original_text=text, english_text=text)
        for i, (who, text) in enumerate(CALL)
    ]
    analyzer = build_analyzer(Settings())
    try:
        analysis = await analyzer.analyze(segments)
    finally:
        await analyzer.aclose()
    assert analysis.summary and analysis.customer_issue
    assert analysis.model.startswith(("sarvam", "claude"))
    assert segments[1].sentiment == Sentiment.negative
    assert all(s.sentiment is not None for s in segments)
