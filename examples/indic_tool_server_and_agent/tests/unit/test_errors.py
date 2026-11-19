"""Unit tests for BodhanError and error codes."""
from __future__ import annotations

import pytest
from bodhan_mcp.core.errors import BodhanError, FILE_NOT_FOUND, INVALID_INPUT, UNSUPPORTED_LANGUAGE


def test_bodhan_error_to_dict():
    err = BodhanError(
        code=FILE_NOT_FOUND,
        message="File does not exist",
        retryable=False,
        hint="Pass a valid path",
    )
    d = err.to_dict()
    assert d["ok"] is False
    assert d["error"]["code"] == FILE_NOT_FOUND
    assert d["error"]["message"] == "File does not exist"
    assert d["error"]["retryable"] is False
    assert d["error"]["hint"] == "Pass a valid path"
