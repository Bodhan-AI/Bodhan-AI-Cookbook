"""Voice selection.

Indic-Speak ships a fixed library and does not clone voices, so the honest goal
is *consistency*, not imitation: one voice per language for the whole video,
overridable per language from ``voices.yaml``.

Because there is no diarization, a multi-speaker lesson is narrated by a single
consistent voice. That is a deliberate trade-off, recorded in the report rather
than hidden: a viewer never hears the narrator change mid-sentence, which is
worse than not matching the original speaker.

    # voices.yaml
    languages:
      hi: Suhani
      ta: Anitha
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import languages


@dataclass
class VoiceMap:
    languages_map: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | str | None) -> VoiceMap:
        if path is None:
            return cls()
        file = Path(path)
        if not file.exists():
            return cls(warnings=[f"voices file {file} does not exist; using language defaults"])
        try:
            import yaml
        except ImportError:  # pragma: no cover - dependency is pinned
            return cls(warnings=["PyYAML is missing; using language defaults"])

        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        mapping = data.get("languages") or {}
        if not isinstance(mapping, dict):
            return cls(warnings=["voices.yaml: 'languages' must be a mapping"])
        cleaned = {str(key): str(value) for key, value in mapping.items() if value}
        return cls(languages_map=cleaned)

    def resolve(self, lang: str, *, preferred: str | None = None) -> tuple[str, list[str]]:
        """Return the voice to use, plus any warnings raised while choosing."""
        warnings = list(self.warnings)
        language = languages.get(lang)

        for candidate in (preferred, self.languages_map.get(lang)):
            if not candidate:
                continue
            known = {voice for group in language.voices.values() for voice in group}
            if candidate in known:
                return candidate, warnings
            warnings.append(
                f"voice {candidate!r} is not in the Indic-Speak library for {lang}; "
                f"falling back to {language.voice('female')}"
            )

        return language.voice("female"), warnings
