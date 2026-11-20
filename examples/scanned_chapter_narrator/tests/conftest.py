"""Pytest fixtures and configuration for Bodhan Lesson."""

import os
import sys
from pathlib import Path

# Enable mock mode during testing to ensure fast, deterministic offline execution
os.environ["BODHAN_MOCK"] = "1"

# Ensure src/ is on sys.path
src_dir = Path(__file__).resolve().parents[1] / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))
