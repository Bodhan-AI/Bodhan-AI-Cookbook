"""Call summary and sentiment, with a fake Sarvam client."""

import pytest

from app.analysis import AnalysisError, CallAnalyzer, parse_answer, transcript_lines
from app.llm import ChatResult
from app.models import Resolution, Segment, Sentiment, SentimentTrend
from app.sarvam_client import SarvamError
from tests.conftest import FakeClaude, FakeSarvam, analysis_answer


def segments() -> list[Segment]:
    return [
        Segment(segment_no=1, start=0, end=4, speaker="Speaker 1", original_text="अ", english_text="It is broken!"),
        Segment(segment_no=2, start=4, end=9, speaker="Speaker 2", original_text="ब", english_text="Let me check."),
        Segment(segment_no=3, start=9, end=12, original_text="", english_text=""),
        Segment(segment_no=4, start=75, end=80, original_text="धन्यवाद", english_text=None, error="Translation failed"),
    ]


def test_transcript_lines_prefer_english_and_skip_empty():
    assert transcript_lines(segments()) == [
        "[1] (00:00) Speaker 1: It is broken!",
        "[2] (00:04) Speaker 2: Let me check.",
        "[4] (01:15): धन्यवाद",
    ]


async def test_analysis_and_segment_tags():
    fake = FakeSarvam()
    segs = segments()
    analysis = await CallAnalyzer(fake).analyze(segs)
    assert analysis.resolution == Resolution.resolved
    assert analysis.overall_sentiment == Sentiment.mixed
    assert analysis.customer_sentiment_trend == SentimentTrend.improved
    assert analysis.action_items == ["Agent to call back tomorrow"]
    assert analysis.model == "sarvam-105b"
    assert [s.sentiment for s in segs] == [Sentiment.negative, Sentiment.neutral, None, Sentiment.positive]
    system, user = fake.calls[0]
    assert system["role"] == "system" and "[2] (00:04) Speaker 2: Let me check." in user["content"]


async def test_no_text_means_no_request():
    fake = FakeSarvam()
    assert await CallAnalyzer(fake).analyze([Segment(segment_no=1, start=0, end=1)]) is None
    assert fake.calls == []


async def test_score_is_clamped_and_unknown_segments_ignored():
    answer = analysis_answer(sentiment_score=3, segment_sentiments=[{"segment_no": 99, "sentiment": "negative"}])
    segs = segments()
    analysis = await CallAnalyzer(FakeSarvam(lambda _m: answer)).analyze(segs)
    assert analysis.sentiment_score == 1.0
    assert segs[0].sentiment == Sentiment.neutral


def test_parse_answer_tolerates_code_fences():
    assert parse_answer('```json\n{"summary": "s", "overall_sentiment": "neutral"}\n```').summary == "s"


@pytest.mark.parametrize("content", ["no json here", '{"summary": "s"}', '{"summary": "s", "overall_sentiment": "x"}'])
def test_parse_answer_rejects_bad_shapes(content):
    with pytest.raises(AnalysisError):
        parse_answer(content)


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        (ChatResult('{"summary": "half', truncated=True), "truncated"),
        (SarvamError(403, "invalid_api_key_error", "bad key"), "sarvam_auth"),
        (SarvamError(402, "insufficient_quota_error", "No credits available."), "sarvam_no_credits"),
        (SarvamError(503, "internal_server_error", "busy"), "internal_server_error"),
    ],
)
async def test_failures_raise_analysis_error(reply, code):
    with pytest.raises(AnalysisError) as e:
        await CallAnalyzer(FakeSarvam(lambda _m: reply)).analyze(segments())
    assert e.value.code == code


async def test_claude_is_the_fallback_when_sarvam_fails():
    sarvam = FakeSarvam(lambda _m: SarvamError(402, "insufficient_quota_error", "No credits available."))
    claude = FakeClaude()
    segs = segments()
    analysis = await CallAnalyzer(sarvam, claude).analyze(segs)
    assert analysis.model == "claude-opus-5-5"
    assert len(sarvam.calls) == 1 and claude.calls == sarvam.calls
    assert segs[0].sentiment == Sentiment.negative


async def test_fallback_also_covers_unusable_answers():
    sarvam = FakeSarvam(lambda _m: ChatResult("not json", truncated=False))
    analysis = await CallAnalyzer(sarvam, FakeClaude()).analyze(segments())
    assert analysis.model == "claude-opus-5-5"


async def test_sarvam_success_never_calls_claude():
    claude = FakeClaude()
    assert (await CallAnalyzer(FakeSarvam(), claude).analyze(segments())).model == "sarvam-105b"
    assert claude.calls == []


async def test_every_model_failing_names_each_reason():
    sarvam = FakeSarvam(lambda _m: SarvamError(402, "insufficient_quota_error", "x"))
    claude = FakeClaude(lambda _m: ChatResult("{", truncated=True))
    with pytest.raises(AnalysisError) as e:
        await CallAnalyzer(sarvam, claude).analyze(segments())
    assert "Sarvam account has no credits" in e.value.message and "Claude answer was cut off" in e.value.message
