"""Pytest configuration and isolation fixtures for Bodhan Tool Agent tests."""

from __future__ import annotations

import tempfile
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def isolate_test_temp_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    """Ensures all tests and any tempfile calls write exclusively inside pytest's tmp_path."""
    test_run_dir = tmp_path / "run"
    test_run_dir.mkdir(parents=True, exist_ok=True)

    # Route default system tempdir to pytest's tmp_path
    monkeypatch.setattr(tempfile, "tempdir", str(test_run_dir))

    orig_mkdtemp = tempfile.mkdtemp

    def safe_mkdtemp(suffix: str = "", prefix: str = "tmp", dir: str | None = None) -> str:
        if dir is None:
            dir = str(test_run_dir)
        return orig_mkdtemp(suffix=suffix, prefix=prefix, dir=dir)

    monkeypatch.setattr(tempfile, "mkdtemp", safe_mkdtemp)

    # Point test class instance directories to tmp_path
    if request.instance:
        request.instance.temp_dir = test_run_dir
        if hasattr(request.instance, "out_dir"):
            request.instance.out_dir = test_run_dir / "out"
            request.instance.out_dir.mkdir(parents=True, exist_ok=True)
        if hasattr(request.instance, "samples_dir"):
            request.instance.samples_dir = test_run_dir / "samples"
            request.instance.samples_dir.mkdir(parents=True, exist_ok=True)

    yield test_run_dir
