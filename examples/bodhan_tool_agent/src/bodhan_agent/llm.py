"""Configurable OpenAI-compatible LLM client adapter.

Provides structured planning, English summarization, and task explanations.
Adheres strictly to the language policy:
NEVER uses the LLM for Indic translation, transcription, OCR, or speech generation.
"""

from __future__ import annotations

import logging
import os
from typing import Any
import openai
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("bodhan_agent.llm")


class LLMClient:
    """Adapter for interacting with any OpenAI-compatible LLM provider."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ):
        self.base_url = (base_url or os.getenv("LLM_BASE_URL", "https://api.openai-compatible.local/v1")).rstrip("/")
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")

        if not self.api_key:
            logger.warning("LLM_API_KEY is not configured; LLM planning will fallback to deterministic rules.")

        # Initialize OpenAI SDK client
        self.client = openai.OpenAI(
            base_url=self.base_url,
            api_key=self.api_key or "placeholder_key",
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and not self.api_key.startswith("your_"))

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        response_format_json: bool = False,
        temperature: float = 0.2,
        max_tokens: int = 2000,
    ) -> str:
        """Call the LLM for reasoning, planning, or English summarization."""
        if not self.is_configured:
            return ""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if response_format_json:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            resp = self.client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"LLM API call failed: {e}")
            return ""

    def summarize_in_english(self, context_text: str, focus: str = "general summary") -> str:
        """Produce a concise, factual summary strictly in English."""
        system_prompt = (
            "You are a professional research analyst. Summarize the provided content accurately in clear, "
            "concise English. Preserve key facts, figures, and technical points. Do not include personal opinions."
        )
        user_prompt = f"Topic/Focus: {focus}\n\nSource Content:\n{context_text[:12000]}"

        if not self.is_configured:
            # Deterministic extraction fallback when no external LLM is configured
            lines = [line.strip() for line in context_text.splitlines() if line.strip() and not line.startswith("#")]
            return "\n\n".join(lines[:5]) if lines else "Summary of provided content."

        res = self.complete(system_prompt=system_prompt, user_prompt=user_prompt)
        return res or "Summary could not be generated."

    def explain_failure(self, task_prompt: str, error_msg: str, steps_completed: list[str]) -> str:
        """Generate a clear explanation of an unavoidable task failure."""
        system_prompt = (
            "You are an engineering assistant explaining an execution failure honestly and constructively. "
            "Explain why the task could not proceed, whether it was due to unsupported language direction, "
            "budget exhaustion, or missing data. Suggest actionable next steps."
        )
        user_prompt = (
            f"User Task: {task_prompt}\n"
            f"Error Encountered: {error_msg}\n"
            f"Completed Steps: {', '.join(steps_completed) if steps_completed else 'None'}"
        )

        if not self.is_configured:
            return f"Task encountered an error: {error_msg}."

        res = self.complete(system_prompt=system_prompt, user_prompt=user_prompt)
        return res or f"Task execution halted: {error_msg}"
