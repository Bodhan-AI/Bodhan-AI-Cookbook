"""Register Bodhan language tools with the stdio MCP server."""

import os
from pathlib import Path
from typing import Annotated, Literal

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field, ValidationError

from .ocr import extract_document_text
from .request_budget import (
    TRANSLATE_PER_TOKEN, TRANSLATION_OUTPUT_LIMIT, active_budget,
)
from .speech import synthesize_speech
from .transcription import transcribe_audio

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

mcp = FastMCP("Bodhan Tool Server")
mcp.add_tool(transcribe_audio)
mcp.add_tool(synthesize_speech)
mcp.add_tool(extract_document_text)
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


class TranslationResult(BaseModel):
    translation: str
    target_language: Language
    script: Literal["native", "roman", "codemix"]
    truncated: bool
    usage: TokenUsage


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
    }
    budget = active_budget.get()
    reservation = None
    if budget is not None:
        payload["max_output_tokens"] = TRANSLATION_OUTPUT_LIMIT
        reservation = budget.reserve("indic-translate", TRANSLATION_OUTPUT_LIMIT * TRANSLATE_PER_TOKEN)
    try:
        response = client.post(
            TRANSLATE_URL,
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
            413: "input_too_large: Pass a shorter passage for this increment.",
            429: "rate_limited: Wait before retrying.",
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


@mcp.tool()
def translate_text(
    text: Annotated[str, Field(min_length=1, description="A short, nonblank passage to translate.")],
    target_language: Annotated[Language, Field(description="Target language code, for example kn for Kannada.")],
) -> TranslationResult:
    """Translate a short passage using Indic-Translate. The source language is inferred.

    Returns the translated text, target language, script and token usage.
    Reports an error if the translation is empty or truncated.
    """
    if not text.strip():
        raise ToolError("empty_input: Provide text with at least one non-whitespace character.")
    api_key = os.getenv("BODHAN_TRANSLATE_KEY", "").strip()
    if not api_key:
        raise ToolError("missing_key: Set BODHAN_TRANSLATE_KEY in the recipe's .env file.")
    with httpx.Client(timeout=20.0) as client:
        return _translate_request(client, text, target_language, api_key)
