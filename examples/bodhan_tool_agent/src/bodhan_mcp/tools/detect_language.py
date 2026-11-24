"""
detect_language tool — Unicode-based language/script detection, no model cost.
"""
from __future__ import annotations

import logging
from pathlib import Path

from bodhan_core.errors import BodhanError, ErrorCode, error_response, success_response
from bodhan_core.langs import LANG_NAMES, detect_script

log = logging.getLogger(__name__)

_MAX_CHARS = 2000


def register(mcp, client, workspace, cost_meter) -> None:  # noqa: ANN001
    @mcp.tool()
    async def detect_language(
        text: str | None = None,
        file_path: str | None = None,
    ) -> dict:
        """Detect the language(s) present in text or in a plain text file. \
Uses Unicode script analysis (free, no model cost). Returns a ranked list of \
candidate language codes with confidence scores. For ambiguous scripts \
(e.g. Devanagari used for both Hindi and Marathi), returns multiple candidates. \
Requires at least one of text or file_path."""
        try:
            if text is None and file_path is None:
                return error_response(
                    ErrorCode.INVALID_ARGUMENT,
                    "At least one of 'text' or 'file_path' must be provided.",
                    "Pass text to analyse or a path to a text file.",
                )

            sample: str = ""

            if file_path is not None:
                try:
                    resolved = workspace.resolve(file_path)
                except BodhanError as exc:
                    return exc.to_response()
                except Exception as exc:  # noqa: BLE001
                    return error_response(
                        ErrorCode.FILE_NOT_FOUND,
                        f"Cannot resolve path: {exc}",
                        "Check that the file path is correct.",
                    )

                if not resolved.exists():
                    return error_response(
                        ErrorCode.FILE_NOT_FOUND,
                        f"File not found: {file_path}",
                        "Provide a path to an existing text file.",
                    )
                try:
                    raw = resolved.read_bytes()
                    sample = raw.decode("utf-8", errors="replace")[:_MAX_CHARS]
                except Exception as exc:  # noqa: BLE001
                    return error_response(
                        ErrorCode.UNSUPPORTED_FORMAT,
                        f"Could not read file: {exc}",
                        "Ensure the file is a plain text file.",
                    )

            if text:
                sample = (sample + " " + text).strip()
            sample = sample[:_MAX_CHARS]

            candidates_raw = detect_script(sample)

            # detect_script returns list of lang codes ordered by confidence.
            # We build confidence scores: first gets 1.0, rest decay.
            candidates = []
            total = len(candidates_raw)
            for idx, code in enumerate(candidates_raw):
                confidence = round(1.0 - (idx * (0.6 / max(total - 1, 1))), 3) if total > 1 else 1.0
                candidates.append({
                    "lang": code,
                    "name": LANG_NAMES.get(code, code),
                    "confidence": max(confidence, 0.1),
                })

            ambiguous = len(candidates) > 1

            return success_response(
                {"candidates": candidates, "ambiguous": ambiguous},
                cost_inr=0.0,
                cached=False,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("detect_language error: %s", exc, exc_info=True)
            return error_response(
                ErrorCode.UPSTREAM_ERROR,
                f"Unexpected error: {exc}",
                "Check input and try again.",
            )
