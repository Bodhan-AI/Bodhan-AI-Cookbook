"""
Unit tests for sandbox and path traversal enforcement (src/bodhan_core/workspace.py).
"""
import pytest
from pathlib import Path
from bodhan_core.workspace import Workspace
from bodhan_core.errors import BodhanError, ErrorCode


def test_workspace_resolve_valid(tmp_path):
    ws = Workspace(tmp_path)
    sub = tmp_path / "data.txt"
    sub.write_text("hello")

    res = ws.resolve("data.txt")
    assert res == sub
    assert res.exists()


def test_workspace_resolve_blocks_traversal(tmp_path):
    ws = Workspace(tmp_path)
    with pytest.raises(BodhanError) as exc_info:
        ws.resolve("../../outside.txt")
    assert exc_info.value.code == ErrorCode.PATH_OUTSIDE_WORKSPACE


def test_workspace_temp_and_artifact_helpers(tmp_path):
    ws = Workspace(tmp_path)
    t = ws.temp_path("test.tmp")
    assert t.parent == tmp_path / "tmp"

    a = ws.artifact_path("summary.txt")
    assert a.parent == tmp_path / "artifacts"
