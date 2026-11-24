"""Tests package initialization: ensures mock mode is active for hermetic unit testing."""

import os
os.environ["BODHAN_MOCK"] = "1"
