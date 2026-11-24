"""Sandboxed file references and exact-input model response cache."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .errors import TaskError


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.root / ".bodhan_cache"
        self.cache_dir.mkdir(exist_ok=True)

    def path(self, relative: str, must_exist: bool = True) -> Path:
        if not relative or "\x00" in relative:
            raise TaskError("INVALID_PATH", "Provide a nonempty file path")
        p = Path(relative).expanduser()
        p = (p if p.is_absolute() else self.root / p).resolve()
        if not p.is_relative_to(self.root):
            raise TaskError("PATH_OUTSIDE_WORKSPACE", "File must be inside BODHAN_WORKSPACE")
        if must_exist and not p.exists():
            raise TaskError("FILE_NOT_FOUND", f"Missing file: {p.name}")
        return p

    def output_path(self, relative: str) -> Path:
        path = self.path(relative, must_exist=False)
        if not path.is_relative_to(self.root / "artifacts"):
            raise TaskError("UNSAFE_OUTPUT_PATH", "Generated files must be stored under artifacts/")
        return path

    def write(self, relative: str, data: str | bytes) -> str:
        path = self.output_path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            path.write_text(data, encoding="utf-8")
        else:
            path.write_bytes(data)
        return str(path)

    def fingerprint(self, path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            while block := f.read(1 << 20):
                h.update(block)
        return h.hexdigest()

    @staticmethod
    def cache_key(model: str, payload: dict) -> str:
        encoded = json.dumps({"model": model, "payload": payload}, sort_keys=True,
                             ensure_ascii=False, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def cached_json(self, key: str) -> dict | None:
        p = self.cache_dir / f"{key}.json"
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
        return None

    def save_json(self, key: str, value: dict) -> None:
        path = self.cache_dir / f"{key}.json"
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def cached_blob(self, key: str) -> bytes | None:
        p = self.cache_dir / f"{key}.bin"
        return p.read_bytes() if p.is_file() else None

    def save_blob(self, key: str, data: bytes) -> None:
        (self.cache_dir / f"{key}.bin").write_bytes(data)
