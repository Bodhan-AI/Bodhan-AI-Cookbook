"""Read-path containment for caller-supplied files.

Write destinations are already confined to the run's artifacts directory by
``resolve_artifact_path``. Reads had no equivalent, so a caller could ask
``read_text_file`` for ``.env`` and the model would happily echo the API key
into its final answer. This module supplies the missing half.

Two modes, mirroring the write path:

* Run context active (``set_active_input_roots`` was called): reads are
  confined to the declared roots.
* Standalone MCP server (no context): reads are allowed, because an operator is
  the caller. Sensitive filenames are still refused in both modes.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from pathlib import Path

from mcp.server.fastmcp.exceptions import ToolError

_active_input_roots: ContextVar[tuple[Path, ...] | None] = ContextVar(
    "active_input_roots", default=None
)

# Refused even inside an allowed root. The recipe root is a legitimate files_dir
# and it holds credentials, so containment alone is not sufficient.
_SENSITIVE_SUFFIXES = frozenset({".pem", ".key", ".pfx", ".p12"})
_SENSITIVE_DIRS = frozenset({".git", ".venv", "node_modules", ".ssh"})


def _is_sensitive(path: Path) -> bool:
    """True for credential files and repository internals."""
    name = path.name.lower()
    if name == ".env" or name.startswith(".env."):
        return True
    if path.suffix.lower() in _SENSITIVE_SUFFIXES:
        return True
    return any(part.lower() in _SENSITIVE_DIRS for part in path.parts)


def set_active_input_roots(roots: list[str | Path] | None) -> Token:
    """Declare the roots a run is allowed to read from. Pass None to allow any."""
    if not roots:
        return _active_input_roots.set(None)
    resolved = tuple(Path(root).expanduser().resolve() for root in roots)
    return _active_input_roots.set(resolved)


def reset_active_input_roots(token: Token) -> None:
    _active_input_roots.reset(token)


def get_active_input_roots() -> tuple[Path, ...] | None:
    return _active_input_roots.get()


def resolve_input_path(file_path: str | Path) -> Path:
    """Resolve a read path, enforcing the sensitive-file and root rules.

    Raises:
        ToolError: for empty paths, credential files, or paths outside the
            active input roots.
    """
    raw = str(file_path).strip()
    if not raw:
        raise ToolError("A file path is required.")

    resolved = Path(raw).expanduser().resolve()

    if _is_sensitive(resolved):
        raise ToolError(
            f"Refusing to read sensitive file '{file_path}'. Credentials and "
            "repository metadata are not readable through tools."
        )

    roots = _active_input_roots.get()
    if roots is not None and not any(
        resolved.is_relative_to(root) for root in roots
    ):
        permitted = ", ".join(str(root) for root in roots)
        raise ToolError(
            f"Path '{file_path}' resolves outside the permitted input roots "
            f"({permitted})."
        )

    return resolved
