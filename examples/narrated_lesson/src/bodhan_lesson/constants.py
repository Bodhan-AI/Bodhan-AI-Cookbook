"""Shared constants for the narrated-lesson pipeline."""

from __future__ import annotations

# Assignment input: folder of PNG/JPG pages (or a PDF rendered to PNG).
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
DEFAULT_DPI = 200

# Layout-detection confidence below this is flagged doubtful (not auto-deleted).
# Bodhan docs do not publish an official cutoff — start at 0.5 and tune later.
LOW_CONF_THRESHOLD = 0.5
