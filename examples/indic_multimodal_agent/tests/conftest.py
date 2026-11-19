"""Pytest configuration and global fixtures.
Ensures unit and integration tests run 100% offline with zero external network or paid calls.
"""

import os
import sys
from pathlib import Path
import pytest

# Ensure tests/ directory is in sys.path
sys.path.insert(0, str(Path(__file__).parent))
from test_doubles import LLMTestDouble


@pytest.fixture(autouse=True)
def offline_test_llm_client(monkeypatch):
    """Automatically patch llm_client during pytest with the offline LLMTestDouble."""
    double_client = LLMTestDouble()
    monkeypatch.setattr("bodhan_agent.agent.llm_client", double_client)
    monkeypatch.setattr("bodhan_agent.llm.llm_client", double_client)
    yield double_client
