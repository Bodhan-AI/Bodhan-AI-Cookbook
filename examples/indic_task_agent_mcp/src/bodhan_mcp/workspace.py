"""File references in and out of the tools.

Media moves between tools as paths, never as base64 in the model context. Reads are limited to the allowed
directories; writes go only to the output directory, with sanitised, collision-free names.
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import Settings
from .errors import ToolError


class Workspace:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.output_dir = settings.output_dir.resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @property
    def roots(self) -> list[Path]:
        return [p.resolve() for p in self.settings.allowed_dirs] + [self.output_dir]

    def resolve_input(self, path: str, *, want_dir: bool = False) -> Path:
        if not path or not str(path).strip():
            raise ToolError("invalid_argument", "A file path is required.", hint="Pass the path of an input file.")
        p = Path(path).expanduser()
        if not p.is_absolute():
            candidates = [Path.cwd() / p, self.output_dir / p] + [r / p for r in self.roots]
            p = next((c for c in candidates if c.exists()), Path.cwd() / p)
        p = p.resolve()
        if not any(p == r or r in p.parents for r in self.roots):
            raise ToolError("path_not_allowed", f"{path} is outside the folders this server may read.",
                            hint="Use a file inside the task's input folder or a file produced by an earlier tool call.",
                            details={"allowed": [str(r) for r in self.roots]})
        if not p.exists():
            raise ToolError("file_not_found", f"{path} does not exist.", hint="Check the path; use inspect_file on the input folder to list files.")
        if want_dir is False and p.is_dir():
            raise ToolError("invalid_argument", f"{path} is a folder, not a file.", hint="Pass one file; inspect_file lists a folder's files.")
        return p

    def output_path(self, name: str | None, default_stem: str, suffix: str, *, keep_name_suffix: bool = False) -> Path:
        """A unique path in the output folder. The extension always matches the content (`suffix`), so a caller asking
        for "speech.wav" gets "speech.mp3" when the tool writes MP3; only write_text honours the caller's extension."""
        stem = Path(name).stem if name else default_stem
        stem = re.sub(r"[^\w.\-]+", "_", stem, flags=re.UNICODE).strip("._") or default_stem
        suffix = suffix if suffix.startswith(".") else f".{suffix}"
        if keep_name_suffix and name and Path(name).suffix:
            suffix = Path(name).suffix
        candidate = self.output_dir / f"{stem}{suffix}"
        n = 2
        while candidate.exists():
            candidate = self.output_dir / f"{stem}_{n}{suffix}"
            n += 1
        return candidate

    def rel(self, path: Path) -> str:
        """Paths are reported relative to the current directory when possible, which keeps tool results short."""
        try:
            return str(path.resolve().relative_to(Path.cwd().resolve()))
        except ValueError:
            return str(path)
