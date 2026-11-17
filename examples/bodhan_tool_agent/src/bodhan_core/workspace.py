"""Workspace path management with sandbox enforcement."""

from __future__ import annotations

import pathlib
import tempfile

from .errors import BodhanError, ErrorCode


class Workspace:
    """Manages all file I/O within a sandboxed root directory.

    Every path exposed to tool implementations must go through
    :meth:`resolve` so that the path-traversal guard is always enforced.
    """

    def __init__(self, root: str | pathlib.Path) -> None:
        self._root = pathlib.Path(root).resolve()

    @property
    def root(self) -> pathlib.Path:
        """The resolved absolute workspace root."""
        return self._root

    # ------------------------------------------------------------------
    # Core path resolution
    # ------------------------------------------------------------------

    def resolve(self, path: str) -> pathlib.Path:
        """Resolve *path* relative to the workspace root.

        Raises:
            BodhanError: ``PATH_OUTSIDE_WORKSPACE`` if the resolved path
                escapes the workspace root.
        """
        candidate = (self._root / path).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError:
            raise BodhanError(
                code=ErrorCode.PATH_OUTSIDE_WORKSPACE,
                message=f"Path '{path}' resolves outside the workspace root.",
                how_to_fix="Use a path that is relative to the workspace root and "
                           "does not contain '..' traversal segments.",
                retryable=False,
            )
        return candidate

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def temp_file(self, suffix: str = "") -> pathlib.Path:
        """Return a unique path inside ``<root>/tmp/`` with the given *suffix*.

        The ``tmp/`` subdirectory is created if necessary.  The returned path
        does not yet exist on disk — the caller is responsible for writing it.
        """
        tmp_dir = self._root / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(suffix=suffix, dir=tmp_dir)
        import os
        os.close(fd)
        return pathlib.Path(name)

    def temp_path(self, name: str) -> pathlib.Path:
        """Return ``<root>/tmp/<name>`` and ensure the directory exists."""
        tmp_dir = self._root / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        return tmp_dir / name

    def artifact_path(self, name: str) -> pathlib.Path:
        """Return ``<root>/artifacts/<name>`` and ensure the directory exists."""
        artifacts_dir = self._root / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        return artifacts_dir / name

    def ensure_dirs(self) -> None:
        """Create ``tmp/`` and ``artifacts/`` subdirectories under the root."""
        (self._root / "tmp").mkdir(parents=True, exist_ok=True)
        (self._root / "artifacts").mkdir(parents=True, exist_ok=True)
