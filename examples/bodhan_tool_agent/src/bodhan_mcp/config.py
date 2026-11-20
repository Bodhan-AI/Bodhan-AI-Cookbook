from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from dataclasses import field
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path.cwd() / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    transcribe_key: str = ""
    translate_key: str = ""
    speak_key: str = ""
    ocr_key: str = ""
    llm_api_key: str = ""
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "openai/gpt-oss-20b"
    groq_rpm_limit: int | None = None
    groq_tpm_limit: int | None = None
    groq_itpm_limit: int | None = None
    groq_otpm_limit: int | None = None
    timeout_seconds: float = 60.0
    max_retries: int = 2
    output_dir: Path = Path("out")

    @classmethod
    def from_env(cls, output_dir: Path | None = None) -> Settings:
        return cls(
            transcribe_key=os.getenv("BODHAN_TRANSCRIBE_KEY", ""),
            translate_key=os.getenv("BODHAN_TRANSLATE_KEY", ""),
            speak_key=os.getenv("BODHAN_SPEAK_KEY", ""),
            ocr_key=os.getenv("BODHAN_OCR_KEY", ""),
            llm_api_key=os.getenv("LLM_API_KEY") or os.getenv("GROQ_API_KEY", ""),
            llm_base_url=os.getenv(
                "LLM_BASE_URL",
                "https://api.groq.com/openai/v1",
            ),
            llm_model=os.getenv("LLM_MODEL", "openai/gpt-oss-20b"),
            groq_rpm_limit=_optional_positive_int("GROQ_RPM_LIMIT"),
            groq_tpm_limit=_optional_positive_int("GROQ_TPM_LIMIT"),
            groq_itpm_limit=_optional_positive_int("GROQ_ITPM_LIMIT"),
            groq_otpm_limit=_optional_positive_int("GROQ_OTPM_LIMIT"),
            timeout_seconds=float(os.getenv("BODHAN_TIMEOUT_SECONDS", "60")),
            max_retries=max(0, min(int(os.getenv("BODHAN_MAX_RETRIES", "2")), 4)),
            output_dir=output_dir or Path(os.getenv("BODHAN_OUTPUT_DIR", "out")),
        )

    def api_key(self, model: str) -> str:
        keys = {
            "indic-transcribe": self.transcribe_key,
            "indic-translate": self.translate_key,
            "indic-speak": self.speak_key,
            "indic-ocr": self.ocr_key,
        }
        key = keys[model]
        if not key:
            from bodhan_mcp.errors import ToolError

            raise ToolError(
                "missing_api_key",
                f"Configure the environment variable for {model} before calling this tool.",
                False,
            )
        return key


def _optional_positive_int(name: str) -> int | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        return None
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return parsed


@dataclass
class CostLedger:
    maximum: Decimal | None = None
    spent: Decimal = Decimal("0")
    reserved: Decimal = Decimal("0")
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def reserve(self, amount: Decimal) -> None:
        if amount < 0:
            raise ValueError("Cost reservation cannot be negative.")
        with self._lock:
            if self.maximum is not None and self.spent + self.reserved + amount > self.maximum:
                from bodhan_mcp.errors import BudgetExceeded

                raise BudgetExceeded()
            self.reserved += amount

    def settle(self, reserved: Decimal, actual: Decimal) -> None:
        if reserved < 0 or actual < 0:
            raise ValueError("Settled costs cannot be negative.")
        with self._lock:
            if reserved > self.reserved:
                raise ValueError("Cannot settle more than the reserved budget.")
            self.reserved -= reserved
            self.spent += actual

    def cancel(self, reserved: Decimal) -> None:
        if reserved < 0:
            raise ValueError("Cancelled costs cannot be negative.")
        with self._lock:
            if reserved > self.reserved:
                raise ValueError("Cannot cancel more than the reserved budget.")
            self.reserved -= reserved
