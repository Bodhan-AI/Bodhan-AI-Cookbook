import os

from openai import OpenAI

from bodhan_mcp import config  # noqa: F401  (importing it loads the .env file)


def make_client():
    """The LLM is any OpenAI-compatible service, set only by these 3 variables."""
    for name in ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        if not os.getenv(name, "").strip():
            raise RuntimeError(f"Missing environment variable {name}. See .env.example.")
    client = OpenAI(base_url=os.environ["LLM_BASE_URL"].strip(), api_key=os.environ["LLM_API_KEY"].strip(), max_retries=0)
    return client, os.environ["LLM_MODEL"].strip()