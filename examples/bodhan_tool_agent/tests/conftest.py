"""Pytest configuration and shared fixtures for Bodhan Tool Agent tests."""

import os
import sys
from pathlib import Path

# Add recipe src to sys.path
RECIPE_SRC = Path(__file__).resolve().parent.parent / "src"
if str(RECIPE_SRC) not in sys.path:
    sys.path.insert(0, str(RECIPE_SRC))

# Ensure mock mode is active during testing so tests run fast, deterministic and offline
os.environ["BODHAN_MOCK"] = "1"
