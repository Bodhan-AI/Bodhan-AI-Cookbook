"""Runtime configuration: CLI args + environment, resolved once into a Settings object."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# 13 production-tier Indic-Speak languages. --langs accepts any of these codes.
# code -> (human name, Indic-Translate target token, Indic-Transcribe lang code)
SUPPORTED_LANGS: dict[str, dict] = {
    "hi": {"name": "Hindi", "translate": "Hindi", "asr": "hi"},
    "ta": {"name": "Tamil", "translate": "Tamil", "asr": "ta"},
    "bn": {"name": "Bengali", "translate": "Bengali", "asr": "bn"},
    "te": {"name": "Telugu", "translate": "Telugu", "asr": "te"},
    "kn": {"name": "Kannada", "translate": "Kannada", "asr": "kn"},
    "ml": {"name": "Malayalam", "translate": "Malayalam", "asr": "ml"},
    "mr": {"name": "Marathi", "translate": "Marathi", "asr": "mr"},
    "gu": {"name": "Gujarati", "translate": "Gujarati", "asr": "gu"},
    "pa": {"name": "Punjabi", "translate": "Punjabi", "asr": "pa"},
    "or": {"name": "Odia", "translate": "Odia", "asr": "or"},
    "as": {"name": "Assamese", "translate": "Assamese", "asr": "as"},
    "ur": {"name": "Urdu", "translate": "Urdu", "asr": "ur"},
    "en": {"name": "English", "translate": "English", "asr": "en"},
}


class ConfigError(RuntimeError):
    """Raised for missing/invalid configuration so the CLI can exit cleanly."""


@dataclass
class Settings:
    # --- inputs / outputs ---
    input: str = ""
    langs: list[str] = field(default_factory=list)
    out: str = "./out"
    glossary_path: str | None = None
    voices_path: str | None = None
    code_mixed: bool = False

    # --- behaviour knobs (extra flags; required interface stays fixed) ---
    mock: bool = False
    burn_subs: bool = False
    sub_font: str = ""
    concurrency: int = 2
    rate_interval: float = 1.0               # seconds between request starts per model (adaptive)
    asr_model: str = "indic-transcribe"     # Core by default
    vad: str = "energy"                      # energy | webrtc | silero
    resume: bool = False
    max_condense_rounds: int = 2
    min_stretch: float = 0.85
    max_stretch: float = 1.2
    target_lufs: float = -16.0
    log_level: str = "INFO"

    # --- endpoints / secrets (from env) ---
    bodhan_base_url: str = "https://api.bodhan.ai"
    transcribe_key: str = ""
    translate_key: str = ""
    speak_key: str = ""
    ocr_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""

    @property
    def cache_dir(self) -> str:
        return os.path.join(self.out, ".cache")

    def lang_name(self, code: str) -> str:
        return SUPPORTED_LANGS.get(code, {}).get("name", code)


def load_settings(args, env: dict | None = None) -> Settings:
    """Merge parsed CLI args with environment into a validated Settings.

    In --mock mode, keys are not required (clients serve fixtures), so the whole
    pipeline runs offline for tests and dry runs.
    """
    load_dotenv()  # pulls .env if present; real env always wins
    env = os.environ if env is None else env

    langs = [c.strip() for c in args.langs.split(",") if c.strip()]
    unknown = [c for c in langs if c not in SUPPORTED_LANGS]
    if unknown:
        raise ConfigError(
            f"Unsupported language code(s): {', '.join(unknown)}. "
            f"Supported: {', '.join(SUPPORTED_LANGS)}"
        )
    if not langs:
        raise ConfigError("--langs must list at least one language code")

    s = Settings(
        input=args.input,
        langs=langs,
        out=args.out,
        glossary_path=getattr(args, "glossary", None),
        voices_path=getattr(args, "voices", None),
        code_mixed=getattr(args, "code_mixed", False),
        mock=getattr(args, "mock", False),
        burn_subs=getattr(args, "burn_subs", False),
        sub_font=getattr(args, "sub_font", "") or "",
        concurrency=max(1, getattr(args, "concurrency", 2)),
        rate_interval=max(0.0, getattr(args, "rate_interval", 1.0)),
        asr_model=getattr(args, "asr_model", "indic-transcribe"),
        vad=getattr(args, "vad", "energy"),
        resume=getattr(args, "resume", False),
        log_level=getattr(args, "log_level", "INFO"),
        bodhan_base_url=env.get("BODHAN_BASE_URL", "https://api.bodhan.ai").rstrip("/"),
        # Per-model keys (assignment spec); fall back to a single BODHAN_API_KEY
        # (Cookbook convention) if a model-specific key is not set.
        transcribe_key=env.get("BODHAN_TRANSCRIBE_KEY") or env.get("BODHAN_API_KEY", ""),
        translate_key=env.get("BODHAN_TRANSLATE_KEY") or env.get("BODHAN_API_KEY", ""),
        speak_key=env.get("BODHAN_SPEAK_KEY") or env.get("BODHAN_API_KEY", ""),
        ocr_key=env.get("BODHAN_OCR_KEY") or env.get("BODHAN_API_KEY", ""),
        llm_base_url=env.get("LLM_BASE_URL", "").rstrip("/"),
        llm_model=env.get("LLM_MODEL", ""),
        llm_api_key=env.get("LLM_API_KEY", ""),
    )

    if not s.mock:
        missing = [
            name
            for name, val in [
                ("BODHAN_TRANSCRIBE_KEY", s.transcribe_key),
                ("BODHAN_TRANSLATE_KEY", s.translate_key),
                ("BODHAN_SPEAK_KEY", s.speak_key),
            ]
            if not val
        ]
        if missing:
            raise ConfigError(
                "Missing required env var(s): "
                + ", ".join(missing)
                + ". Set them in .env (see .env.example) or pass --mock for an offline dry run."
            )
    return s
