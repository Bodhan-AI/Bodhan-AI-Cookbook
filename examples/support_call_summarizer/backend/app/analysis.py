"""Call summary and sentiment from one LLM request over the whole transcript.

Sarvam (``sarvam-105b``) is asked first; if it fails, Claude is the fallback.

The transcript is sent as numbered lines (English where available, otherwise the
original language). The model answers with JSON matching ``ANSWER_SCHEMA``: the
call-level analysis plus the segments that are clearly positive or negative.
Segments it does not mention are neutral. Listing only the non-neutral ones keeps
the answer short, so a long call fits in ``max_tokens`` with reasoning on.
"""

from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, Field, ValidationError

from .claude_client import ClaudeClient
from .config import Settings
from .llm import ChatClient, LLMError
from .models import CallAnalysis, Resolution, Segment, Sentiment, SentimentTrend
from .sarvam_client import SarvamClient

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You analyse customer support phone calls for a support team.
You get the call transcript as numbered lines: [segment number] (start time) speaker: text.
The text is usually an English machine translation of an Indian-language call, so expect \
some odd wording. Speaker labels, when present, come from an automatic model and do not \
say who is the agent and who is the customer; work that out from what they say.

Answer only with a JSON object with these fields:
- summary: 3 to 5 plain English sentences: why the customer called, what the agent did, how it ended.
- customer_issue: the customer's problem or request in one sentence.
- resolution: "resolved", "partially_resolved", "unresolved", or "unclear".
- action_items: follow-ups promised or still needed (e.g. "Agent to call back within 24 hours"). Empty if none.
- topics: 1 to 5 short topic tags, e.g. "refund", "internet outage".
- overall_sentiment: the customer's sentiment over the whole call: "positive", "neutral", "negative", or "mixed".
- sentiment_score: a number from -1 (very negative) to 1 (very positive).
- customer_sentiment_trend: how the customer's mood changed from start to end: "improved", "steady", or "worsened".
- sentiment_explanation: one or two sentences on what drove the sentiment.
- segment_sentiments: only the segments whose tone is clearly "positive" or "negative", \
as {"segment_no": n, "sentiment": "..."}. Leave out neutral segments.

Base everything on the transcript only. Do not invent names, numbers or promises."""

ANSWER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "summary",
        "customer_issue",
        "resolution",
        "action_items",
        "topics",
        "overall_sentiment",
        "sentiment_score",
        "customer_sentiment_trend",
        "sentiment_explanation",
        "segment_sentiments",
    ],
    "properties": {
        "summary": {"type": "string"},
        "customer_issue": {"type": "string"},
        "resolution": {"type": "string", "enum": [r.value for r in Resolution]},
        "action_items": {"type": "array", "items": {"type": "string"}},
        "topics": {"type": "array", "items": {"type": "string"}},
        "overall_sentiment": {"type": "string", "enum": [s.value for s in Sentiment]},
        "sentiment_score": {"type": "number"},
        "customer_sentiment_trend": {"type": "string", "enum": [t.value for t in SentimentTrend]},
        "sentiment_explanation": {"type": "string"},
        "segment_sentiments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["segment_no", "sentiment"],
                "properties": {
                    "segment_no": {"type": "integer"},
                    "sentiment": {"type": "string", "enum": ["positive", "negative"]},
                },
            },
        },
    },
}

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "call_analysis", "schema": ANSWER_SCHEMA, "strict": True},
}


class AnalysisError(Exception):
    """Analysis failed; ``message`` is shown to the user as a warning."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _SegmentTag(BaseModel):
    segment_no: int
    sentiment: Sentiment


class _Answer(BaseModel):
    summary: str
    customer_issue: str = ""
    resolution: Resolution = Resolution.unclear
    action_items: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    overall_sentiment: Sentiment
    sentiment_score: float = 0.0
    customer_sentiment_trend: SentimentTrend = SentimentTrend.steady
    sentiment_explanation: str = ""
    segment_sentiments: list[_SegmentTag] = Field(default_factory=list)


class CallAnalyzer:
    """Tries each client in order (e.g. Sarvam, then Claude) until one gives a usable analysis."""

    def __init__(self, *clients: ChatClient):
        if not clients:
            raise ValueError("CallAnalyzer needs at least one LLM client")
        self.clients = clients

    async def aclose(self) -> None:
        for client in self.clients:
            await client.aclose()

    async def analyze(self, segments: list[Segment]) -> CallAnalysis | None:
        """Fill ``segment.sentiment`` in place and return the call analysis.

        Returns None when there is no text to analyse. Raises ``AnalysisError``
        when every client failed.
        """
        lines = transcript_lines(segments)
        if not lines:
            return None
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Transcript:\n" + "\n".join(lines)},
        ]
        failures: list[AnalysisError] = []
        for client in self.clients:
            try:
                answer = await _ask(client, messages)
            except AnalysisError as exc:
                log.warning("%s analysis failed: %s", client.provider, exc.code)
                failures.append(exc)
                continue
            if failures:
                log.info("analysis done by the fallback %s (%s)", client.provider, client.model)
            _tag_segments(segments, answer)
            return _analysis(answer, client.model)
        if len(failures) == 1:
            raise failures[0]
        raise AnalysisError(failures[-1].code, " ".join(f.message for f in failures))


async def _ask(client: ChatClient, messages: list[dict]) -> _Answer:
    name = client.provider
    try:
        result = await client.chat(messages, response_format=RESPONSE_FORMAT)
    except LLMError as exc:
        if exc.is_auth_error:
            message = f"The server's {name} API key is missing or was rejected."
            raise AnalysisError(f"{name.lower()}_auth", message) from exc
        if exc.is_out_of_credit:
            raise AnalysisError(f"{name.lower()}_no_credits", f"The {name} account has no credits left.") from exc
        raise AnalysisError(exc.code, f"{name} did not answer ({exc.code}).") from exc
    if result.truncated:
        raise AnalysisError("truncated", f"The {name} answer was cut off (raise its MAX_TOKENS setting).")
    return parse_answer(result.content, name)


def _tag_segments(segments: list[Segment], answer: _Answer) -> None:
    tags = {t.segment_no: t.sentiment for t in answer.segment_sentiments if t.sentiment != Sentiment.mixed}
    for seg in segments:
        seg.sentiment = tags.get(seg.segment_no, Sentiment.neutral) if _text(seg) else None


def _analysis(answer: _Answer, model: str) -> CallAnalysis:
    return CallAnalysis(
        summary=answer.summary.strip(),
        customer_issue=answer.customer_issue.strip(),
        resolution=answer.resolution,
        action_items=[a.strip() for a in answer.action_items if a.strip()],
        topics=[t.strip() for t in answer.topics if t.strip()],
        overall_sentiment=answer.overall_sentiment,
        sentiment_score=round(max(-1.0, min(1.0, answer.sentiment_score)), 2),
        customer_sentiment_trend=answer.customer_sentiment_trend,
        sentiment_explanation=answer.sentiment_explanation.strip(),
        model=model,
    )


def transcript_lines(segments: list[Segment]) -> list[str]:
    lines = []
    for seg in segments:
        text = _text(seg)
        if text:
            start = int(seg.start)
            who = f" {seg.speaker}" if seg.speaker else ""
            lines.append(f"[{seg.segment_no}] ({start // 60:02d}:{start % 60:02d}){who}: {text}")
    return lines


def parse_answer(content: str, provider: str = "The model") -> _Answer:
    # json_schema output should be bare JSON, but tolerate ```json fences or text around it
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        raise AnalysisError("unexpected_response", f"{provider} did not return a JSON analysis.")
    try:
        return _Answer.model_validate(json.loads(match.group(0)))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise AnalysisError("unexpected_response", f"{provider} returned an analysis in an unexpected shape.") from exc


def _text(seg: Segment) -> str:
    return (seg.english_text or seg.original_text or "").strip()


def build_analyzer(settings: Settings) -> CallAnalyzer | None:
    """Sarvam first, then Claude as the fallback; None when neither key is set."""
    clients: list[ChatClient] = []
    if settings.sarvam_api_key:
        clients.append(
            SarvamClient(
                settings.sarvam_api_key,
                model=settings.sarvam_model,
                base_url=settings.sarvam_base_url,
                reasoning_effort=settings.sarvam_reasoning_effort,
                max_tokens=settings.sarvam_max_tokens,
                timeout=settings.sarvam_timeout_seconds,
                max_retries=settings.sarvam_max_retries,
                rpm=settings.sarvam_rpm,
            )
        )
    if settings.anthropic_api_key:
        clients.append(
            ClaudeClient(
                settings.anthropic_api_key,
                model=settings.anthropic_model,
                effort=settings.anthropic_effort,
                max_tokens=settings.anthropic_max_tokens,
                timeout=settings.anthropic_timeout_seconds,
                max_retries=settings.anthropic_max_retries,
            )
        )
    return CallAnalyzer(*clients) if clients else None
