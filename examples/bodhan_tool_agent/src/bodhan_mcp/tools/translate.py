"""
translate_text tool — text/file translation via Bodhan Indic-Translate.

Splits input into paragraph-level chunks to stay within model limits.
Cost: ₹0.20 per 10,000 output tokens.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Literal

from bodhan_core.cost import estimate_translate
from bodhan_core.errors import BodhanError, ErrorCode, error_response, success_response
from bodhan_core.langs import TRANSLATE_LANGS, is_supported

log = logging.getLogger(__name__)

# Rough token estimate: 1 token ≈ 4 chars
_CHARS_PER_TOKEN = 4
# Maximum chars per translation segment
_MAX_SEGMENT_CHARS = 2000


# ── paragraph splitter ────────────────────────────────────────────────────────

_TABLE_ROW = re.compile(r"^\|.+\|$")
_MD_HEADING = re.compile(r"^#{1,6}\s")


def _split_paragraphs(text: str, preserve_markdown: bool = True) -> list[str]:
    """
    Split text into logical segments suitable for translation.

    Rules:
    - Blank lines delimit paragraphs.
    - Markdown headings are atomic units.
    - Consecutive table rows are grouped as one unit.
    - Any segment longer than _MAX_SEGMENT_CHARS is further split on sentence
      boundaries (. ! ?) to keep API payloads reasonable.
    """
    segments: list[str] = []
    current_lines: list[str] = []
    in_table = False

    def flush() -> None:
        nonlocal in_table
        block = "\n".join(current_lines).strip()
        if block:
            segments.append(block)
        current_lines.clear()
        in_table = False

    for line in text.splitlines():
        stripped = line.strip()

        if preserve_markdown and _TABLE_ROW.match(stripped):
            if not in_table:
                flush()
            in_table = True
            current_lines.append(line)
            continue

        if in_table and not _TABLE_ROW.match(stripped):
            flush()

        if not stripped:
            flush()
            continue

        if preserve_markdown and _MD_HEADING.match(stripped):
            flush()
            segments.append(line)
            continue

        current_lines.append(line)

    flush()

    # Sub-split oversized segments
    final: list[str] = []
    for seg in segments:
        if len(seg) <= _MAX_SEGMENT_CHARS:
            final.append(seg)
        else:
            # Split on sentence boundaries
            pieces = re.split(r"(?<=[.!?])\s+", seg)
            current = ""
            for piece in pieces:
                if len(current) + len(piece) + 1 > _MAX_SEGMENT_CHARS and current:
                    final.append(current.strip())
                    current = piece
                else:
                    current = (current + " " + piece).strip() if current else piece
            if current:
                final.append(current.strip())

    return [s for s in final if s.strip()]


# ── register ──────────────────────────────────────────────────────────────────

def register(mcp, client, workspace, cost_meter) -> None:  # noqa: ANN001
    @mcp.tool()
    async def translate_text(
        text: str | None = None,
        file_path: str | None = None,
        source_lang: str | None = None,
        target_lang: str = "hi",
        preserve_markdown: bool = True,
    ) -> dict:
        """Translate text to a target Indic language using Bodhan \
Indic-Translate. Source language is auto-detected if not given. Accepts raw \
text or a path to a plain text or Markdown file. Handles long inputs by \
splitting into paragraphs. Tables and Markdown structure are preserved when \
preserve_markdown=True. Returns UNSUPPORTED_LANGUAGE if target_lang is not \
supported (supported: as,bn,brx,doi,en,gu,hi,kn,kok,ks,ks-Deva,mai,ml,mni,\
mni-Beng,mr,ne,or,pa,sa,sat,sd,sd-Arab,ta,te,ur). \
Cost: ₹0.20 per 10,000 output tokens."""
        t0 = time.monotonic()
        try:
            if text is None and file_path is None:
                return error_response(
                    ErrorCode.INVALID_ARGUMENT,
                    "At least one of 'text' or 'file_path' must be provided.",
                    "Pass text directly or supply a file_path.",
                )

            # 1. Load from file if needed
            source_text = text or ""
            output_file: str | None = None

            if file_path is not None:
                try:
                    resolved = workspace.resolve(file_path)
                except BodhanError as exc:
                    return exc.to_response()
                except Exception as exc:  # noqa: BLE001
                    return error_response(
                        ErrorCode.FILE_NOT_FOUND,
                        f"Cannot resolve path: {exc}",
                        "Check the file path.",
                    )
                if not resolved.exists():
                    return error_response(
                        ErrorCode.FILE_NOT_FOUND,
                        f"File not found: {file_path}",
                        "Provide a path to an existing text or Markdown file.",
                    )
                try:
                    source_text = resolved.read_text(encoding="utf-8", errors="replace")
                except Exception as exc:  # noqa: BLE001
                    return error_response(
                        ErrorCode.UNSUPPORTED_FORMAT,
                        f"Could not read file: {exc}",
                        "Ensure the file is a plain-text or Markdown file.",
                    )

            if not source_text.strip():
                return error_response(
                    ErrorCode.INVALID_ARGUMENT,
                    "Input text is empty.",
                    "Provide non-empty text to translate.",
                )

            # 2. Validate target language
            if not is_supported(target_lang, "translate"):
                return error_response(
                    ErrorCode.UNSUPPORTED_LANGUAGE,
                    f"Language '{target_lang}' is not supported for translation.",
                    f"Supported: {', '.join(sorted(TRANSLATE_LANGS))}.",
                )

            # 3. Split
            segments = _split_paragraphs(source_text, preserve_markdown)
            segment_count = len(segments)

            # 4. Estimate cost
            estimated_tokens = len(source_text) // _CHARS_PER_TOKEN
            cost_inr = estimate_translate(estimated_tokens)
            if not cost_meter.check_budget(cost_inr, remaining=float("inf")):
                return error_response(
                    ErrorCode.BUDGET_EXCEEDED,
                    "Estimated cost exceeds the configured budget.",
                    "Raise BODHAN_BUDGET_INR or use a shorter text.",
                )

            # 5. Translate each segment
            translated_parts: list[str] = []
            detected_source: str | None = source_lang
            succeeded = 0

            for seg in segments:
                result = await client.translate(
                    seg,
                    target_lang=target_lang,
                    source_lang=source_lang,
                )
                translated_parts.append(result.get("translation") or result.get("translated_text") or seg)
                if detected_source is None:
                    detected_source = result.get("source_lang_detected")
                succeeded += 1

            # 6. Rejoin
            # Blank-line separated blocks
            translated_text = "\n\n".join(translated_parts)
            coverage_ratio = round(succeeded / max(segment_count, 1), 3)

            # 7. Write output file when input was a file
            if file_path is not None:
                stem = Path(file_path).stem
                out_path = workspace.temp_path(f"{stem}.translated.txt")
                out_path.write_text(translated_text, encoding="utf-8")
                output_file = str(out_path)

            # 8. Record cost
            actual_tokens = len(translated_text) // _CHARS_PER_TOKEN
            actual_cost = estimate_translate(actual_tokens)
            cost_meter.add("translate", actual_cost)

            duration_ms = int((time.monotonic() - t0) * 1000)
            data: dict = {
                "translated_text": translated_text,
                "source_lang_detected": detected_source or "unknown",
                "segment_count": segment_count,
                "coverage_ratio": coverage_ratio,
            }
            if output_file:
                data["output_file"] = output_file

            return success_response(
                data,
                cost_inr=actual_cost,
                cached=False,
                duration_ms=duration_ms,
            )

        except BodhanError as exc:
            return exc.to_response()
        except Exception as exc:  # noqa: BLE001
            log.error("translate_text error: %s", exc, exc_info=True)
            return error_response(
                ErrorCode.UPSTREAM_ERROR,
                f"Unexpected error: {exc}",
                "Check logs and retry.",
                retryable=True,
            )
