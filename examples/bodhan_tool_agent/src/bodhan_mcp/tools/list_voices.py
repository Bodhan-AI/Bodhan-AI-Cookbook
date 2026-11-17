"""
list_voices tool — returns TTS voice catalogue, no model cost.
"""
from __future__ import annotations

import logging

from bodhan_core.errors import ErrorCode, error_response, success_response
from bodhan_core.langs import LANG_NAMES, SPEAK_LANGS, SPEAK_STYLES, VOICES

log = logging.getLogger(__name__)


def register(mcp, client, workspace, cost_meter) -> None:  # noqa: ANN001
    @mcp.tool()
    async def list_voices(language: str | None = None) -> dict:
        """List the available text-to-speech voices. If language is specified \
(ISO code), returns voices for that language only. Otherwise returns all voices \
grouped by language. Also lists available speaking styles. No model cost."""
        try:
            if language is not None:
                if language not in SPEAK_LANGS:
                    return error_response(
                        ErrorCode.UNSUPPORTED_LANGUAGE,
                        f"Language '{language}' is not supported for TTS.",
                        f"Supported languages: {', '.join(sorted(SPEAK_LANGS))}.",
                    )
                filtered = {language: VOICES.get(language, {"female": [], "male": []})}
            else:
                filtered = dict(VOICES)

            # Annotate each language entry with its display name
            annotated: dict[str, dict] = {}
            for code, genders in filtered.items():
                annotated[code] = {
                    "name": LANG_NAMES.get(code, code),
                    "female": genders.get("female", []),
                    "male": genders.get("male", []),
                }

            return success_response(
                {
                    "voices": annotated,
                    "styles": SPEAK_STYLES,
                    "languages_supported": sorted(SPEAK_LANGS),
                },
                cost_inr=0.0,
                cached=False,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("list_voices error: %s", exc, exc_info=True)
            return error_response(
                ErrorCode.UPSTREAM_ERROR,
                f"Unexpected error: {exc}",
                "Try again.",
            )
