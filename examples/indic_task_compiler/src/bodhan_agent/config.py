"""Configuration and quiet .env loading (never prints or logs secret values)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def load_env_quietly(extra_search_dirs: list[Path] | None = None) -> None:
    """Load .env if present without overwriting already-exported env vars or echoing values."""
    candidates: list[Path] = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ]
    if extra_search_dirs:
        candidates.extend(d / ".env" for d in extra_search_dirs)
    for path in candidates:
        if path.is_file():
            load_dotenv(path, override=False)
            return


@dataclass
class RunConfig:
    task: str
    input_paths: list[Path]
    out_dir: Path
    max_steps: int = 30
    max_cost_inr: float = 20.0
    timeout_s: float = 150.0
    max_replans: int = 2
    cer_threshold: float = 0.45
    use_stdio_mcp: bool = True
    cache_dir: Path | None = None
    llm_base_url: str = field(default_factory=lambda: os.environ.get("LLM_BASE_URL", ""))
    llm_model: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", ""))
    llm_api_key: str = field(default_factory=lambda: os.environ.get("LLM_API_KEY", ""))
