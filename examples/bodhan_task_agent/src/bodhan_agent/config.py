from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MAX_STEPS = 30
DEFAULT_MAX_COST_INR = 20.0
DEFAULT_DEADLINE_S = 290.0

LLM_ENV = ("LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")
_NONE_WORDS = frozenset({"", "none", "null", "off"})


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name, default))
    except ValueError:
        return default


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(env.get(name, default))
    except ValueError:
        return default


def _optional_float(env: Mapping[str, str], name: str, default: float | None) -> float | None:
    raw = env.get(name)
    if raw is None:
        return default
    if raw.strip().lower() in _NONE_WORDS:
        return None
    try:
        return float(raw)
    except ValueError:
        return default


def _absolute(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


@dataclass(frozen=True)
class LlmSettings:
    base_url: str
    model: str
    api_key: str = field(repr=False)
    timeout_s: float = 90.0
    max_retries: int = 2
    temperature: float | None = 0.0
    max_output_tokens: int = 4096

    def __post_init__(self) -> None:
        checks = {
            "timeout_s": self.timeout_s > 0,
            "max_retries": self.max_retries >= 0,
            "temperature": self.temperature is None or 0 <= self.temperature <= 2,
            "max_output_tokens": self.max_output_tokens >= 256,
        }
        invalid = [name for name, ok in checks.items() if not ok]
        if invalid:
            raise ValueError(f"Invalid LLM settings: {', '.join(invalid)}")

    @property
    def missing(self) -> list[str]:
        values = {"LLM_BASE_URL": self.base_url, "LLM_MODEL": self.model, "LLM_API_KEY": self.api_key}
        return [name for name in LLM_ENV if not values[name]]

    @property
    def configured(self) -> bool:
        return not self.missing

    @staticmethod
    def from_env(env: Mapping[str, str] | None = None) -> LlmSettings:
        env = os.environ if env is None else env
        return LlmSettings(
            base_url=env.get("LLM_BASE_URL", "").strip().rstrip("/"),
            model=env.get("LLM_MODEL", "").strip(),
            api_key=env.get("LLM_API_KEY", "").strip(),
            timeout_s=max(1.0, _float(env, "LLM_TIMEOUT_S", 90.0)),
            max_retries=max(0, _int(env, "LLM_MAX_RETRIES", 2)),
            temperature=_optional_float(env, "LLM_TEMPERATURE", 0.0),
            max_output_tokens=max(256, _int(env, "LLM_MAX_OUTPUT_TOKENS", 4096)),
        )


@dataclass(frozen=True)
class RunSettings:
    task: str
    files: tuple[Path, ...]
    out_dir: Path
    max_steps: int = DEFAULT_MAX_STEPS
    max_cost_inr: float = DEFAULT_MAX_COST_INR
    deadline_s: float = DEFAULT_DEADLINE_S
    max_llm_turns: int | None = None
    max_parallel_calls: int = 6
    read_max_chars: int = 12_000
    observation_text_chars: int = 1_500
    mcp_grace_s: float = 10.0
    python: str = sys.executable

    def __post_init__(self) -> None:
        checks = {
            "files": all(path.is_absolute() for path in self.files),
            "out_dir": self.out_dir.is_absolute(),
            "max_steps": self.max_steps >= 1,
            "max_cost_inr": self.max_cost_inr >= 0,
            "deadline_s": self.deadline_s > 0,
            "max_llm_turns": self.max_llm_turns is None or self.max_llm_turns >= 1,
            "max_parallel_calls": self.max_parallel_calls >= 1,
            "read_max_chars": self.read_max_chars >= 1,
            "observation_text_chars": self.observation_text_chars >= 0,
            "mcp_grace_s": self.mcp_grace_s >= 0,
        }
        invalid = [name for name, ok in checks.items() if not ok]
        if invalid:
            raise ValueError(f"Invalid run settings: {', '.join(invalid)}")

    @staticmethod
    def create(
        task: str,
        files: list[str] | tuple[str, ...],
        out_dir: str,
        max_steps: int = DEFAULT_MAX_STEPS,
        max_cost_inr: float = DEFAULT_MAX_COST_INR,
        deadline_s: float = DEFAULT_DEADLINE_S,
    ) -> RunSettings:
        return RunSettings(
            task=task,
            files=tuple(dict.fromkeys(_absolute(path) for path in files)),
            out_dir=_absolute(out_dir),
            max_steps=max_steps,
            max_cost_inr=max_cost_inr,
            deadline_s=deadline_s,
        )

    @property
    def llm_turn_limit(self) -> int:
        return self.max_llm_turns if self.max_llm_turns is not None else 2 * self.max_steps

    @property
    def artifacts_dir(self) -> Path:
        return self.out_dir / "artifacts"

    @property
    def work_dir(self) -> Path:
        return self.out_dir / "work"

    @property
    def literals_dir(self) -> Path:
        return self.work_dir / "literals"

    @property
    def trace_path(self) -> Path:
        return self.out_dir / "trace.jsonl"

    @property
    def report_path(self) -> Path:
        return self.out_dir / "report.json"

    @property
    def result_path(self) -> Path:
        return self.out_dir / "result.md"

    @property
    def server_log_path(self) -> Path:
        return self.work_dir / "server.log"

    @property
    def server_input_dir(self) -> Path:
        existing = [path for path in self.files if path.exists()]
        if len(existing) == 1 and existing[0].is_dir():
            return existing[0]
        folders = [path if path.is_dir() else path.parent for path in existing]
        if not folders:
            return self.work_dir
        return Path(os.path.commonpath([str(folder) for folder in folders]))

    def server_env(self, base: Mapping[str, str] | None = None) -> dict[str, str]:
        env = dict(os.environ if base is None else base)
        env.update(
            {
                "BODHAN_MCP_INPUT_DIR": str(self.server_input_dir),
                "BODHAN_MCP_ALLOWED_DIRS": os.pathsep.join(str(path) for path in self.files if path.exists()),
                "BODHAN_MCP_OUTPUT_DIR": str(self.work_dir),
                "BODHAN_MCP_LOG_FILE": str(self.server_log_path),
                "BODHAN_MCP_MAX_COST_INR": f"{self.max_cost_inr:.6f}",
                "PYTHONUNBUFFERED": "1",
            }
        )
        for name in LLM_ENV:
            env.pop(name, None)
        return env
