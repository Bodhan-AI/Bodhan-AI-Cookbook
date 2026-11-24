"""Artifact management and content-addressed file storage."""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Optional, Union

from bodhan_mcp.core.errors import BodhanError, INVALID_INPUT


class FileStore:
    def __init__(self, base_dir: Optional[Union[str, Path]] = None):
        if base_dir:
            self.base_dir = Path(base_dir)
        else:
            self.base_dir = Path("./out/artifacts")
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save_bytes(self, data: bytes, extension: str, prefix: str = "art") -> str:
        """Save raw bytes with content-hash name, returning absolute path."""
        h = hashlib.sha256(data).hexdigest()[:16]
        ext = extension if extension.startswith(".") else f".{extension}"
        filename = f"{prefix}_{h}{ext}"
        dest = self.base_dir / filename
        dest.write_bytes(data)
        return str(dest.resolve())

    def save_text(self, text: str, extension: str = ".txt", prefix: str = "text") -> str:
        """Save text data to file, returning absolute path."""
        return self.save_bytes(text.encode("utf-8"), extension=extension, prefix=prefix)

    def import_file(self, src_path: str, prefix: str = "imported") -> str:
        """Copy external file into artifacts with hash-based name."""
        src = Path(src_path)
        if not src.exists():
            raise BodhanError(INVALID_INPUT, f"Source file does not exist: {src_path}")
        data = src.read_bytes()
        return self.save_bytes(data, extension=src.suffix, prefix=prefix)

    def save_artifact(self, content_or_path: Union[str, bytes], name: str, out_dir: Optional[str] = None) -> str:
        """Save artifact (either bytes, string content, or existing file path) to target dir."""
        target_dir = Path(out_dir) if out_dir else self.base_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        if isinstance(content_or_path, str) and os.path.exists(content_or_path):
            src = Path(content_or_path)
            dest = target_dir / (name or src.name)
            shutil.copy2(src, dest)
            return str(dest.resolve())
        elif isinstance(content_or_path, bytes):
            dest = target_dir / name
            dest.write_bytes(content_or_path)
            return str(dest.resolve())
        elif isinstance(content_or_path, str):
            dest = target_dir / name
            dest.write_text(content_or_path, encoding="utf-8")
            return str(dest.resolve())
        else:
            raise BodhanError(INVALID_INPUT, f"Unsupported content type for save_artifact: {type(content_or_path)}")


ArtifactStore = FileStore
default_file_store = FileStore()
