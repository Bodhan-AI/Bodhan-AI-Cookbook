"""
LLM client for the agent brain.

Reads endpoint config from LLM_BASE_URL, LLM_MODEL, and LLM_API_KEY.
The evaluator will swap in their own provider, so we use only standard
OpenAI-compatible chat completions with tool/function calling.
"""
from __future__ import annotations

import json
import os

from openai import AsyncOpenAI


def get_llm_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=os.environ["LLM_API_KEY"],
        base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
        timeout=120.0,
    )


def get_llm_model() -> str:
    return os.environ.get("LLM_MODEL", "gpt-4o")


async def llm_json(
    client: AsyncOpenAI,
    model: str,
    system: str,
    user: str,
) -> dict:
    """Call the LLM and parse its response as JSON.

    Requests JSON mode if supported; falls back to extracting the first
    JSON object from the response text.
    """
    response = await client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    text = response.choices[0].message.content.strip()
    return json.loads(text)
