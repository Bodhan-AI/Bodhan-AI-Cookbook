"""Test configuration and shared fixtures for Bodhan MCP & Task Agent."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure bodhan_mcp and bodhan_agent can be imported
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Dummy key for mocked unit tests if none exists
if "BODHAN_API_KEY" not in os.environ:
    os.environ["BODHAN_API_KEY"] = "mock_test_key_for_testing_12345678"
