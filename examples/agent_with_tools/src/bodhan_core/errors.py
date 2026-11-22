"""The one structured error shape every tool returns instead of raising."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class ToolError(BaseModel):
    code: str  # stable, machine-readable, e.g. "unsupported_language"
    message: str  # actionable, written for the caller (an LLM or a person)
    retryable: bool = False  # true when trying the same call again later may succeed


def check_input_file(path: "Path", param: str, kinds: str) -> ToolError | None:
    """Validate a file reference passed by the caller. Returns an actionable error, or None when it is readable."""
    if not path.exists():
        return ToolError(
            code="file_not_found",
            message=f"`{param}` points to `{path}`, which does not exist (the server resolves relative paths from {Path.cwd()}). "
            f"Pass the path of an existing {kinds} file, preferably absolute, and do not paste file contents.",
        )
    if not path.is_file():
        return ToolError(code="not_a_file", message=f"`{param}` points to `{path}`, which is a directory. Pass the path of a single {kinds} file.")
    try:
        with path.open("rb"):
            pass
    except OSError as exc:
        return ToolError(code="file_unreadable", message=f"Cannot read `{path}`: {exc.strerror or exc}. Check the file's permissions or that no other program locks it.")
    return None
