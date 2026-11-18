#!/usr/bin/env python3
"""Pytest configuration for bodhan_tool_agent tests."""
import sys
from pathlib import Path

_examples = Path(__file__).resolve().parent.parent
_src = _examples / "src"
# Install src/ on path so `from bodhan_mcp.*` works in tests.
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))
# Allow `from src.bodhan_mcp.*` for test consistency.
if str(_examples) not in sys.path:
    sys.path.insert(0, str(_examples))
