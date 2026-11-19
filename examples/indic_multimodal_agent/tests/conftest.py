"""Pytest configuration and global fixtures.
Ensures unit and integration tests run 100% offline with zero external network or paid calls.
"""

import os
import sys
from pathlib import Path
import pytest

# Ensure recipe's src/ and tests/ take precedence in sys.path
recipe_dir = Path(__file__).resolve().parent.parent
src_dir = str(recipe_dir / "src")
tests_dir = str(recipe_dir / "tests")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)
if tests_dir not in sys.path:
    sys.path.insert(0, tests_dir)

# Ensure test default environment variables exist for offline test execution
os.environ.setdefault("LLM_BASE_URL", "https://test-llm.local/v1")
os.environ.setdefault("LLM_MODEL", "gemini-flash-lite-latest")
os.environ.setdefault("LLM_API_KEY", "test-key")

from test_doubles import LLMTestDouble


@pytest.fixture(autouse=True)
def offline_test_llm_client(monkeypatch):
    """Automatically patch llm_client during pytest with the offline LLMTestDouble."""
    double_client = LLMTestDouble()
    monkeypatch.setattr("bodhan_agent.agent.llm_client", double_client)
    monkeypatch.setattr("bodhan_agent.llm.llm_client", double_client)
    yield double_client
