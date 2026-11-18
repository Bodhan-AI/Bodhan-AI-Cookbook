from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    bodhan_api_key: str
    bodhan_base_url: str
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    max_steps: int
    max_budget_inr: float
    timeout_seconds: float
    max_retries: int

    @classmethod
    def from_env(cls, require_llm: bool = True) -> Settings:
        load_dotenv()

        def get(name: str, default: str = "") -> str:
            return os.environ.get(name, default).strip()

        required = ["BODHAN_API_KEY"]
        if require_llm:
            required.extend(["LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"])
        missing = [name for name in required if not get(name)]
        if missing:
            raise ConfigurationError("Missing required environment variable(s): " + ", ".join(missing) + ". Copy .env.example to .env and fill values locally.")
        try:
            steps, budget = int(get("MAX_STEPS", "8")), float(get("MAX_BUDGET_INR", "10"))
            timeout, retries = float(get("REQUEST_TIMEOUT_SECONDS", "60")), int(get("MAX_RETRIES", "2"))
        except ValueError as exc:
            raise ConfigurationError("Step, budget, timeout, and retry settings must be numeric.") from exc
        if steps < 1 or budget < 0 or timeout <= 0 or retries < 0:
            raise ConfigurationError("Limits must be positive (budget may be zero; retries may be zero).")
        return cls(
            get("BODHAN_API_KEY"),
            get("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/"),
            get("LLM_BASE_URL").rstrip("/"),
            get("LLM_MODEL"),
            get("LLM_API_KEY"),
            steps,
            budget,
            timeout,
            retries,
        )
