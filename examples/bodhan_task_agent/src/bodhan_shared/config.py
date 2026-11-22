"""Configuration uses environment variables; local .env is ignored by git."""
import os
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """A conservative dotenv reader; NEVER prints credentials or overwrites env vars."""
    f = Path(path)
    if not f.is_file():
        return
    for raw in f.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.isidentifier():
            os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def key_for(capability: str) -> str:
    k = os.environ.get(f"BODHAN_{capability.upper()}_KEY", "")
    if not k or k.startswith("replace-"):
        k = os.environ.get("BODHAN_API_KEY", "")
    if not k or k.startswith("replace-"):
        raise RuntimeError(f"Missing BODHAN_{capability.upper()}_KEY; see .env.example")
    return k
