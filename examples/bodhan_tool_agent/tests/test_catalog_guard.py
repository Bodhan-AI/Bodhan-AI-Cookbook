"""Guards for the model-facing tool catalog and read-path containment.

These tests exist because catalog exposure was previously implicit: every
FastMCP tool was advertised to the LLM, so adding a helper for internal use
silently widened the model's capabilities. The allowlist is now explicit and
pinned here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from src.bodhan_agent.agent import (
    MODEL_FACING_TOOLS,
    _get_openai_tool_definitions,
)
from src.bodhan_mcp.server import mcp
from src.common.paths import (
    reset_active_input_roots,
    resolve_input_path,
    set_active_input_roots,
)

OPERATOR_ONLY_TOOLS = {"get_cache_stats", "clear_cache"}


# ---------------------------------------------------------------------------
# Catalog exposure
# ---------------------------------------------------------------------------

def test_model_facing_catalog_equals_allowlist():
    advertised = {tool["function"]["name"] for tool in _get_openai_tool_definitions()}
    assert advertised == set(MODEL_FACING_TOOLS)


def test_every_advertised_tool_has_a_description_and_schema():
    for tool in _get_openai_tool_definitions():
        function = tool["function"]
        assert function["description"].strip(), function["name"]
        assert function["parameters"].get("type") == "object", function["name"]


def test_operator_tools_registered_but_never_advertised():
    registered = {tool.name for tool in mcp._tool_manager.list_tools()}
    assert OPERATOR_ONLY_TOOLS <= registered, "cache admin tools must stay callable"

    advertised = {tool["function"]["name"] for tool in _get_openai_tool_definitions()}
    assert not (OPERATOR_ONLY_TOOLS & advertised), (
        "operator tools must not be visible to the model"
    )


def test_chunk_audio_is_gone():
    registered = {tool.name for tool in mcp._tool_manager.list_tools()}
    assert "chunk_audio" not in registered


def test_bypass_cache_is_not_advertised():
    for tool in _get_openai_tool_definitions():
        properties = tool["function"]["parameters"].get("properties", {})
        assert "bypass_cache" not in properties, tool["function"]["name"]


def test_cache_decorator_still_sees_bypass_cache():
    """Hiding the parameter from the schema must not remove it from signatures."""
    import inspect

    from src.bodhan_mcp.server import synthesize_speech, translate_text

    for func in (translate_text, synthesize_speech):
        assert "bypass_cache" in inspect.signature(func).parameters


# ---------------------------------------------------------------------------
# Read-path containment
# ---------------------------------------------------------------------------

def test_read_inside_roots_allowed(tmp_path: Path):
    allowed = tmp_path / "inputs"
    allowed.mkdir()
    target = allowed / "notes.txt"
    target.write_text("hello", encoding="utf-8")

    token = set_active_input_roots([allowed])
    try:
        assert resolve_input_path(target) == target.resolve()
    finally:
        reset_active_input_roots(token)


def test_read_outside_roots_blocked(tmp_path: Path):
    allowed = tmp_path / "inputs"
    allowed.mkdir()
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("secret", encoding="utf-8")

    token = set_active_input_roots([allowed])
    try:
        with pytest.raises(ToolError, match="outside the permitted input roots"):
            resolve_input_path(outside)
    finally:
        reset_active_input_roots(token)


def test_traversal_out_of_roots_blocked(tmp_path: Path):
    allowed = tmp_path / "inputs"
    allowed.mkdir()
    outside = tmp_path / "escape.txt"
    outside.write_text("secret", encoding="utf-8")

    token = set_active_input_roots([allowed])
    try:
        with pytest.raises(ToolError):
            resolve_input_path(allowed / ".." / "escape.txt")
    finally:
        reset_active_input_roots(token)


@pytest.mark.parametrize("name", [".env", ".env.local", "server.pem", "id.key"])
def test_credential_files_blocked_even_inside_roots(tmp_path: Path, name: str):
    target = tmp_path / name
    target.write_text("LLM_API_KEY=sk-should-never-leak", encoding="utf-8")

    token = set_active_input_roots([tmp_path])
    try:
        with pytest.raises(ToolError, match="sensitive file"):
            resolve_input_path(target)
    finally:
        reset_active_input_roots(token)


def test_repository_metadata_blocked(tmp_path: Path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    target = git_dir / "config"
    target.write_text("[core]", encoding="utf-8")

    token = set_active_input_roots([tmp_path])
    try:
        with pytest.raises(ToolError, match="sensitive file"):
            resolve_input_path(target)
    finally:
        reset_active_input_roots(token)


def test_no_context_allows_operator_reads(tmp_path: Path):
    """Standalone MCP mode has no run context, so an operator may read anywhere."""
    target = tmp_path / "operator.txt"
    target.write_text("ok", encoding="utf-8")

    token = set_active_input_roots(None)
    try:
        assert resolve_input_path(target) == target.resolve()
    finally:
        reset_active_input_roots(token)
