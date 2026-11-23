"""
Language registry loader and validator.
Reads languages.yaml, validates targets, and exposes metadata and default voices.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field


class VoiceConfig(BaseModel):
    female: List[str] = Field(default_factory=list)
    male: List[str] = Field(default_factory=list)


class LanguageInfo(BaseModel):
    code: str
    name: str
    script: str
    tts_voices: Optional[VoiceConfig] = None
    code_mixed_target: Optional[str] = None
    unicode_range: Optional[List[int]] = None
    cps_hint: Optional[Any] = None


class LanguageRegistry:
    """Loads and queries language definitions from languages.yaml."""

    def __init__(self, yaml_path: Optional[Path] = None):
        if yaml_path is None:
            # Default to languages.yaml in project root
            yaml_path = Path(__file__).resolve().parent.parent.parent / "languages.yaml"
            if not yaml_path.exists():
                # Or current working directory
                yaml_path = Path("languages.yaml")

        self.yaml_path = yaml_path
        self._load()

    def _load(self) -> None:
        if not self.yaml_path.exists():
            raise FileNotFoundError(f"languages.yaml registry not found at {self.yaml_path}")

        with open(self.yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        self.production_tier: Dict[str, LanguageInfo] = {}
        for code, info in data.get("production_tier", {}).items():
            self.production_tier[code] = LanguageInfo(**info)

        self.preview_tier: Dict[str, Any] = data.get("preview_tier", {})
        self.indic_script_ranges: List[List[int]] = data.get("indic_script_ranges", [])

    def get_supported_target_codes(self) -> List[str]:
        """Returns valid target language codes (excluding source 'en')."""
        return [c for c in self.production_tier.keys() if c != "en"]

    def validate_target_langs(self, langs: List[str]) -> List[str]:
        """
        Validates a list of target language codes.
        Raises ValueError with clear message if unknown code or 'en' is requested.
        Returns deduplicated list in order.
        """
        supported = self.get_supported_target_codes()
        deduped: List[str] = []
        for raw in langs:
            lang = raw.strip().lower()
            if not lang:
                continue
            if lang == "en":
                raise ValueError(
                    "Target language cannot be English ('en'). Input video is already in English. "
                    f"Supported target languages are: {', '.join(supported)}"
                )
            if lang not in self.production_tier:
                raise ValueError(
                    f"Unsupported target language '{lang}'. "
                    f"Supported production-tier languages are: {', '.join(supported)}"
                )
            if lang not in deduped:
                deduped.append(lang)

        if not deduped:
            raise ValueError(f"No target languages specified. Supported: {', '.join(supported)}")
        return deduped

    def get_info(self, code: str) -> Optional[LanguageInfo]:
        return self.production_tier.get(code)

    def get_voices(self, lang: str) -> List[str]:
        """Returns all available voice names (female and male) for a given language."""
        info = self.production_tier.get(lang)
        if not info or not info.tts_voices:
            return []
        voices: List[str] = []
        if info.tts_voices.female:
            voices.extend(info.tts_voices.female)
        if info.tts_voices.male:
            voices.extend(info.tts_voices.male)
        return voices

    def get_default_voice(
        self, lang: str, gender: str = "female", custom_voices: Optional[Dict[str, Any]] = None
    ) -> str:
        """Resolves voice ID for a given language and preferred gender."""
        # 1. Custom voice file override if provided
        if custom_voices and lang in custom_voices:
            v_spec = custom_voices[lang]
            if isinstance(v_spec, dict):
                if gender in v_spec:
                    return v_spec[gender]
                if "default" in v_spec:
                    return v_spec["default"]
            elif isinstance(v_spec, str):
                return v_spec

        # 2. Registry default
        info = self.production_tier.get(lang)
        if info and info.tts_voices:
            voices = getattr(info.tts_voices, gender, None)
            if voices and len(voices) > 0:
                return voices[0]
            # fallback to female or male
            if info.tts_voices.female:
                return info.tts_voices.female[0]
            if info.tts_voices.male:
                return info.tts_voices.male[0]

        # 3. Cross-language fallback
        return "Kavya" if gender == "female" else "Amit"

    get_voice = get_default_voice
