from hashlib import sha256
import json
from pathlib import Path
from typing import Any


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def make_key(endpoint: str, model: str, params: dict[str, Any], payload: bytes) -> str:
    canonical = _canonical({
        "endpoint": endpoint,
        "model": model,
        "params": params,
    }).encode("utf-8")
    digest = sha256()
    digest.update(canonical)
    digest.update(b"\0")
    digest.update(payload)
    return digest.hexdigest()


class Cache:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _entry_path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> bytes | dict[str, Any] | None:
        path = self._entry_path(key)
        if not path.is_file():
            return None
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            return None
        if isinstance(parsed, dict) and "_bytes" in parsed:
            return bytes(parsed["_bytes"])
        return parsed

    def put(self, key: str, value: bytes | dict[str, Any]) -> None:
        path = self._entry_path(key)
        stored: Any = {"_bytes": list(value)} if isinstance(value, bytes) else value
        temporary = path.with_suffix(".tmp")
        temporary.write_text(_canonical(stored), encoding="utf-8", newline="\n")
        temporary.replace(path)
