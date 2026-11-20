
import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


def get_llm_client() -> OpenAI:
    """
    Create an OpenAI-compatible LLM client.

    Provider settings are loaded from environment variables.
    """

    api_key = os.getenv("LLM_API_KEY")
    base_url = os.getenv("LLM_BASE_URL")
    model = os.getenv("LLM_MODEL")

    if not api_key:
        raise ValueError("LLM_API_KEY is not configured.")

    if not base_url:
        raise ValueError("LLM_BASE_URL is not configured.")

    if not model:
        raise ValueError("LLM_MODEL is not configured.")

    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=30,
        max_retries=2,
    )


def get_model_name() -> str:
    """Return the configured LLM model name."""

    load_dotenv()

    model = os.getenv("LLM_MODEL")

    if not model:
        raise ValueError("LLM_MODEL is not configured.")

    return model
