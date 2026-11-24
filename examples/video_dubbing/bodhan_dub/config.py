"""Settings, loaded from the environment exactly once."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from .errors import ConfigError

DEFAULT_BASE_URL = "https://api.bodhan.ai"

# Model ids are lower-case and case-sensitive. Anything else is rejected by the
# Cookbook validator, and would be rejected by the API too.
MODEL_ASR = "indic-transcribe"
MODEL_MT = "indic-translate"
MODEL_TTS = "indic-speak"
MODEL_OCR = "indic-ocr"

_KEY_ENV = {
    "transcribe": "BODHAN_TRANSCRIBE_KEY",
    "translate": "BODHAN_TRANSLATE_KEY",
    "speak": "BODHAN_SPEAK_KEY",
    "ocr": "BODHAN_OCR_KEY",
}

# Accepted spellings, because consoles hand keys out per model and people
# naturally write *_API_KEY. The canonical names above always win.
_KEY_ALIASES = {
    "transcribe": ("BODHAN_TRANSCRIBE_API_KEY",),
    "translate": ("BODHAN_TRANSLATE_API_KEY",),
    "speak": ("BODHAN_SPEAK_API_KEY",),
    "ocr": ("BODHAN_OCR_API_KEY",),
}

_MODEL_FOR = {
    "transcribe": MODEL_ASR,
    "translate": MODEL_MT,
    "speak": MODEL_TTS,
    "ocr": MODEL_OCR,
}


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    model: str
    api_key: str

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.model and self.api_key)


@dataclass(frozen=True)
class Settings:
    base_url: str
    keys: dict[str, str]
    timeout_s: float = 120.0
    max_attempts: int = 4
    max_throttle_retries: int = 8
    backoff_base_s: float = 1.5
    backoff_cap_s: float = 30.0
    max_inflight: int = 4
    rpm: int = 120
    cache_dir: Path = field(default_factory=lambda: Path(".cache"))
    cache_enabled: bool = True
    log_level: str = "INFO"
    llm: LLMConfig | None = None
    tts_style: str = "educational lecture"
    block_chars: int = 250
    max_tempo: float = 1.20
    min_tempo: float = 0.85
    max_spill_s: float = 0.80

    def key_for(self, model: str) -> str:
        """Return the key for a capability, or raise with instructions."""
        key = self.keys.get(model, "")
        if key:
            return key
        names = " or ".join((_KEY_ENV[model], *_KEY_ALIASES[model]))
        raise ConfigError(
            f"no API key for {_MODEL_FOR[model]}. Set {names} in your environment "
            f"or .env (get one at https://console.bodhan.ai), "
            f"or set BODHAN_API_KEY to use one key for every model."
        )

    def has_key(self, model: str) -> bool:
        return bool(self.keys.get(model))

    def model_id(self, capability: str) -> str:
        return _MODEL_FOR[capability]

    def to_public_dict(self) -> dict[str, object]:
        """Settings that are safe to write into a report — never key values."""
        return {
            "base_url": self.base_url,
            "keys_present": {cap: self.has_key(cap) for cap in _KEY_ENV},
            "timeout_s": self.timeout_s,
            "max_attempts": self.max_attempts,
            "max_inflight": self.max_inflight,
            "rpm": self.rpm,
            "cache_enabled": self.cache_enabled,
            "llm_enabled": bool(self.llm and self.llm.enabled),
            "tts_style": self.tts_style,
        }


def _float(env: dict[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _int(env: dict[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _bool(env: dict[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _key_for(env: dict[str, str], capability: str) -> str:
    for name in (_KEY_ENV[capability], *_KEY_ALIASES[capability]):
        value = env.get(name, "").strip()
        if value:
            return value
    return ""


def load_env_file() -> None:
    """Load a .env from, in order: an explicit path, the nearest one, or ~/.config.

    The last option matters: `make check` fails if any `.env` exists inside the
    repository, so the tidy place for working keys is outside it.
    """
    explicit = os.environ.get("BODHAN_DUB_ENV_FILE", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if path.exists():
            load_dotenv(path, override=False)
        return
    found = find_dotenv(usecwd=True)
    if found:
        load_dotenv(found, override=False)
        return
    home_env = Path.home() / ".config" / "bodhan" / ".env"
    if home_env.exists():
        load_dotenv(home_env, override=False)


def load_settings(env: dict[str, str] | None = None, *, dotenv: bool = True) -> Settings:
    """Build settings from the environment.

    ``env`` is injectable so tests never touch the real environment.
    """
    if env is None:
        if dotenv:
            load_env_file()
        env = dict(os.environ)

    fallback = env.get("BODHAN_API_KEY", "").strip()
    keys = {cap: (_key_for(env, cap) or fallback) for cap in _KEY_ENV}

    base_url = env.get("BODHAN_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/") or DEFAULT_BASE_URL

    llm: LLMConfig | None = None
    llm_base = env.get("LLM_BASE_URL", "").strip().rstrip("/")
    llm_model = env.get("LLM_MODEL", "").strip()
    llm_key = env.get("LLM_API_KEY", "").strip()
    if llm_base and llm_model and llm_key:
        llm = LLMConfig(base_url=llm_base, model=llm_model, api_key=llm_key)

    cache_dir = Path(env.get("BODHAN_DUB_CACHE", ".cache")).expanduser()

    return Settings(
        base_url=base_url,
        keys=keys,
        timeout_s=_float(env, "BODHAN_DUB_TIMEOUT_S", 120.0),
        max_attempts=_int(env, "BODHAN_DUB_MAX_ATTEMPTS", 4),
        max_throttle_retries=max(1, _int(env, "BODHAN_DUB_MAX_THROTTLE_RETRIES", 8)),
        backoff_base_s=_float(env, "BODHAN_DUB_BACKOFF_BASE_S", 1.5),
        max_inflight=max(1, _int(env, "BODHAN_DUB_MAX_INFLIGHT", 4)),
        rpm=max(1, _int(env, "BODHAN_DUB_RPM", 120)),
        cache_dir=cache_dir,
        cache_enabled=_bool(env, "BODHAN_DUB_CACHE_ENABLED", True),
        log_level=env.get("BODHAN_DUB_LOG_LEVEL", "INFO").strip().upper() or "INFO",
        llm=llm,
        tts_style=env.get("BODHAN_DUB_TTS_STYLE", "educational lecture").strip() or "educational lecture",
        block_chars=max(60, _int(env, "BODHAN_DUB_BLOCK_CHARS", 250)),
        max_tempo=_float(env, "BODHAN_DUB_MAX_TEMPO", 1.20),
        min_tempo=_float(env, "BODHAN_DUB_MIN_TEMPO", 0.85),
        max_spill_s=_float(env, "BODHAN_DUB_MAX_SPILL_S", 0.80),
    )
