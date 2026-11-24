"""Translation tool with long text chunking and Markdown structure preservation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from ..client.client import ASR_ONLY_LANGUAGES, BodhanClient, TRANSLATE_LANGUAGES, normalize_language_code
from ..models import TranslateResult

PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


def _split_markdown_table(table_text: str, max_chunk_chars: int = 1500) -> List[str]:
    """Split a large Markdown table on row boundaries while preserving header + separator."""
    lines = [l for l in table_text.splitlines() if l.strip()]
    if len(lines) <= 2 or len(table_text) <= max_chunk_chars:
        return [table_text]
    header = lines[:2] if len(lines) >= 2 and "---" in lines[1] else []
    body = lines[len(header):]
    chunks: List[str] = []
    cur = list(header)
    cur_len = sum(len(l) + 1 for l in cur)
    for row in body:
        if cur_len + len(row) + 1 > max_chunk_chars and len(cur) > len(header):
            chunks.append("\n".join(cur))
            cur = list(header) + [row]
            cur_len = sum(len(l) + 1 for l in cur)
        else:
            cur.append(row)
            cur_len += len(row) + 1
    if len(cur) > len(header) or not chunks:
        chunks.append("\n".join(cur))
    return chunks


def _split_into_paragraphs(text: str, max_chunk_chars: int = 1500) -> List[str]:
    """Split text preserving paragraphs, Markdown tables, and reasonable chunk sizes."""
    raw_paras = [p.strip() for p in PARAGRAPH_SPLIT.split(text) if p.strip()]
    chunks = []
    current_chunk = []
    current_length = 0

    for p in raw_paras:
        if _is_table_or_structure(p):
            if current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = []
                current_length = 0
            chunks.extend(_split_markdown_table(p, max_chunk_chars=max_chunk_chars))
            continue

        # If single paragraph is extremely long, split on sentence terminators
        if len(p) > max_chunk_chars:
            sentences = re.split(r"(?<=[।.!?\n])\s+", p)
            for s in sentences:
                s = s.strip()
                if not s:
                    continue
                if current_length + len(s) > max_chunk_chars and current_chunk:
                    chunks.append("\n\n".join(current_chunk))
                    current_chunk = [s]
                    current_length = len(s)
                else:
                    current_chunk.append(s)
                    current_length += len(s)
        else:
            if current_length + len(p) > max_chunk_chars and current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = [p]
                current_length = len(p)
            else:
                current_chunk.append(p)
                current_length += len(p)

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks if chunks else [text]


def _is_table_or_structure(text: str) -> bool:
    """Detect if chunk contains markdown tables or special layout."""
    lines = text.strip().splitlines()
    table_lines = [l for l in lines if l.strip().startswith("|") and l.strip().endswith("|")]
    return len(table_lines) >= 2


def bodhan_translate(
    text: Optional[str] = None,
    file_path: Optional[str] = None,
    target_language: str = "en",
    script: str = "native",
    max_output_tokens: Optional[int] = None,
    client: Optional[BodhanClient] = None,
) -> TranslateResult:
    """Translate text between English and 22 Indian languages.
    
    Handles documents of arbitrary length by splitting paragraphs, translating
    each chunk, and reassembling while preserving layout and Markdown tables.
    """
    if file_path:
        p = Path(file_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Input file not found: {file_path}")
        text_content = p.read_text(encoding="utf-8")
    elif text is not None:
        text_content = text
    else:
        raise ValueError("Either 'text' or 'file_path' must be provided to bodhan_translate.")

    if not text_content or not text_content.strip():
        raise ValueError("Input text for translation cannot be empty.")

    if not target_language or not isinstance(target_language, str):
        raise ValueError("'target_language' must be a valid ISO language code.")

    if script not in ("native", "roman", "codemix"):
        raise ValueError(f"Unsupported script '{script}'. Must be one of: native, roman, codemix.")

    target_lang = normalize_language_code(target_language)
    if target_lang in ASR_ONLY_LANGUAGES:
        raise ValueError(
            f"Target language '{target_language}' is ASR-only (Indic-Transcribe) and is not supported by Indic-Translate."
        )
    # Check language support
    if target_lang not in TRANSLATE_LANGUAGES:
        raise ValueError(
            f"Target language '{target_language}' is not supported by Indic-Translate. "
            f"Supported languages: {', '.join(sorted(TRANSLATE_LANGUAGES))}"
        )

    b_client = client or BodhanClient()

    def _translate_with_retry(chunk_text: str, depth: int = 0) -> TranslateResult:
        """Translate a chunk; if truncated=True and splittable, split in half and retry."""
        res = b_client.translate(
            text=chunk_text,
            target_language=target_lang,
            script=script,
            max_output_tokens=max_output_tokens,
        )
        if res.truncated and len(chunk_text) > 200 and depth < 2:
            if _is_table_or_structure(chunk_text):
                lines = chunk_text.splitlines()
                mid_line = max(1, len(lines) // 2)
                left_txt = "\n".join(lines[:mid_line]).strip()
                right_txt = "\n".join(lines[mid_line:]).strip()
                joiner = "\n"
            else:
                mid = len(chunk_text) // 2
                split_at = chunk_text.find(" ", mid)
                if split_at == -1:
                    split_at = mid
                left_txt = chunk_text[:split_at].strip()
                right_txt = chunk_text[split_at:].strip()
                joiner = " "
            left = _translate_with_retry(left_txt, depth + 1)
            right = _translate_with_retry(right_txt, depth + 1)
            return TranslateResult(
                translation=f"{left.translation}{joiner}{right.translation}".strip(),
                source_language=left.source_language or right.source_language,
                target_language=target_lang,
                script=script,
                output_tokens=left.output_tokens + right.output_tokens,
                cost_inr=round(left.cost_inr + right.cost_inr, 4),
                cached=left.cached and right.cached,
                truncated=left.truncated or right.truncated,
            )
        return res

    # Short input: translate directly (with split-and-retry if truncated)
    if len(text_content) <= 1500:
        return _translate_with_retry(text_content)

    # Multi-chunk translation
    chunks = _split_into_paragraphs(text_content)
    translated_parts = []
    total_tokens = 0
    total_cost = 0.0
    detected_source = None
    any_truncated = False

    for chunk in chunks:
        res = _translate_with_retry(chunk)
        translated_parts.append(res.translation)
        total_tokens += res.output_tokens
        total_cost += res.cost_inr
        if not detected_source and res.source_language:
            detected_source = res.source_language
        if res.truncated:
            any_truncated = True

    combined_translation = "\n\n".join(translated_parts)
    return TranslateResult(
        translation=combined_translation,
        source_language=detected_source,
        target_language=target_lang,
        script=script,
        output_tokens=total_tokens,
        cost_inr=round(total_cost, 4),
        cached=False,
        truncated=any_truncated,
    )
