"""Register Bodhan language tools with the stdio MCP server."""

import os
from collections.abc import Awaitable, Callable
from functools import partial, wraps
from pathlib import Path
from typing import Annotated, Literal

import httpx
from anyio import CapacityLimiter, to_thread
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field, ValidationError

from .api_requests import MAX_CONCURRENT_REQUESTS, post_bodhan
from .document_chunks import MAX_SOURCE_BYTES, document_parts
from .ocr import extract_document_text
from .request_budget import (
    TRANSLATE_PER_TOKEN,
    TRANSLATION_OUTPUT_LIMIT,
    active_budget,
)
from .speech import synthesize_speech
from .transcription import transcribe_audio

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

mcp = FastMCP("Bodhan Tool Server")
_tool_workers = CapacityLimiter(MAX_CONCURRENT_REQUESTS)
TRANSLATE_URL = "https://api.bodhan.ai/v1/chat/completions"

Language = Literal[
    "as", "bn", "brx", "doi", "en", "gu", "hi", "kn", "kok", "ks", "ks-Deva",
    "mai", "ml", "mni", "mni-Beng", "mr", "ne", "or", "pa", "sa", "sat",
    "sd", "sd-Arab", "ta", "te", "ur",
]


class TokenUsage(BaseModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class TranslationSegment(BaseModel):
    """Unicode code-point ranges of one translated source span and its output."""

    source_start: int
    source_end: int
    output_start: int
    output_end: int


class TranslationResult(BaseModel):
    translation: str
    target_language: Language
    script: Literal["native", "roman", "codemix"]
    truncated: bool
    usage: TokenUsage
    segments: list[TranslationSegment] = Field(default_factory=list)


class ChatMessage(BaseModel):
    content: str


class ChatChoice(BaseModel):
    message: ChatMessage
    finish_reason: str


class ChatResponse(BaseModel):
    choices: list[ChatChoice] = Field(min_length=1, max_length=1)
    usage: TokenUsage
    truncated: bool = False


def _translate_request(
    client: httpx.Client, text: str, target_language: Language, api_key: str
) -> TranslationResult:
    payload = {
        "model": "indic-translate",
        "messages": [{"role": "user", "content": text}],
        "target_language_code": target_language,
        "target_script": "native",
        "max_output_tokens": TRANSLATION_OUTPUT_LIMIT,
    }
    budget = active_budget.get()
    try:
        response, reservation = post_bodhan(
            client, TRANSLATE_URL, "indic-translate", TRANSLATION_OUTPUT_LIMIT * TRANSLATE_PER_TOKEN,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
        )
    except httpx.TimeoutException:
        raise ToolError("timeout: Bodhan did not respond within the request timeout.") from None
    except httpx.RequestError:
        raise ToolError("connection_error: Could not reach the Bodhan translation API.") from None

    if response.status_code != 200:
        messages = {
            400: "invalid_request: Check the source text and target language.",
            401: "authentication_failed: Check the Bodhan translation key.",
            403: "access_denied: Check that the key has translation access.",
            413: "input_too_large: Try a shorter source passage.",
        }
        raise ToolError(
            messages.get(response.status_code, f"upstream_error: Bodhan returned HTTP {response.status_code}.")
        )
    try:
        result = ChatResponse.model_validate_json(response.content, strict=True)
    except ValidationError:
        raise ToolError("invalid_response: Bodhan returned an unexpected translation response.") from None

    if budget is not None and reservation is not None:
        budget.settle(reservation, result.usage.completion_tokens * TRANSLATE_PER_TOKEN)
    choice = result.choices[0]
    if result.truncated or choice.finish_reason == "length":
        raise ToolError("incomplete_translation: Bodhan truncated the result. Try a shorter passage.")
    if choice.finish_reason != "stop":
        raise ToolError("incomplete_translation: Bodhan did not finish the translation.")
    if not choice.message.content.strip():
        raise ToolError("empty_translation: Bodhan returned no translated text.")
    return TranslationResult(
        translation=choice.message.content,
        target_language=target_language,
        script="native",
        truncated=False,
        usage=result.usage,
    )


def _translate_document(client: httpx.Client, text: str, language: Language, key: str) -> TranslationResult:
    output = []
    segments = []
    usage = TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    cached: dict[str, str] = {}
    source_offset = output_offset = 0
    for part in document_parts(text):
        rendered = part.value
        if part.mode is not None:
            request = part.for_request()
            if request not in cached:
                try:
                    result = _translate_request(client, request, language, key)
                except ToolError as exc:
                    raise type(exc)(f"Translation segment {len(segments) + 1}: {exc}") from None
                cached[request] = result.translation
                usage.prompt_tokens += result.usage.prompt_tokens
                usage.completion_tokens += result.usage.completion_tokens
                usage.total_tokens += result.usage.total_tokens
            rendered = part.render(cached[request])
            segments.append(TranslationSegment(
                source_start=source_offset, source_end=source_offset + len(part.value),
                output_start=output_offset, output_end=output_offset + len(rendered),
            ))
        output.append(rendered)
        source_offset += len(part.value)
        output_offset += len(rendered)
    return TranslationResult(
        translation="".join(output), target_language=language, script="native",
        truncated=False, usage=usage, segments=segments,
    )


def translate_text(
    text: Annotated[str, Field(min_length=1, description="Nonblank passage or document text to translate.")],
    target_language: Annotated[Language, Field(description="Target language code, for example kn for Kannada.")],
) -> TranslationResult:
    """Translate text into the target language using Indic-Translate.

    Splits long passages inside the tool and preserves common Markdown layout,
    fenced code, and Markdown and HTML table structure. Returns translated text,
    aggregate usage, and source/output ranges for each translated segment.
    An empty, failed, or truncated translation segment stops the call.
    """
    if not text.strip():
        raise ToolError("empty_input: Provide text with at least one non-whitespace character.")
    if len(text.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise ToolError("input_too_large: Source text exceeds 128,000 UTF-8 bytes.")
    api_key = os.getenv("BODHAN_TRANSLATE_KEY", "").strip()
    if not api_key:
        raise ToolError("missing_key: Set BODHAN_TRANSLATE_KEY in the recipe's .env file.")
    with httpx.Client(timeout=20.0) as client:
        return _translate_document(client, text, target_language, api_key)


def _on_worker[Result](tool: Callable[..., Result]) -> Callable[..., Awaitable[Result]]:
    # Keep the signature and docstring that FastMCP uses to publish its schemas.
    @wraps(tool)
    async def call(**arguments) -> Result:
        return await to_thread.run_sync(partial(tool, **arguments), limiter=_tool_workers)

    return call


for tool in (transcribe_audio, synthesize_speech, extract_document_text, translate_text):
    mcp.add_tool(_on_worker(tool))
