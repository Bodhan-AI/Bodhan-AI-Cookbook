"""
Indic-Translate Tool: Preserves Markdown structure, tables, LaTeX, and handles chunking.
"""

import re
from typing import List
from ..models import TranslateInput, TranslateResult, ToolCallMetadata, SUPPORTED_LANGUAGES
from ..client import BodhanClient, BodhanAPIError
from ..pricing import calculate_translate_cost


def split_text_into_chunks(text: str, max_chunk_chars: int = 3000) -> List[str]:
    """
    Split text into logical chunks respecting paragraph boundaries, tables, and lists.
    """
    if len(text) <= max_chunk_chars:
        return [text]

    paragraphs = text.split("\n\n")
    chunks = []
    current_chunk = []
    current_length = 0

    for para in paragraphs:
        para_len = len(para) + 2
        if current_length + para_len > max_chunk_chars and current_chunk:
            chunks.append("\n\n".join(current_chunk))
            current_chunk = [para]
            current_length = para_len
        else:
            current_chunk.append(para)
            current_length += para_len

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks


def run_translate(args: TranslateInput, client: BodhanClient) -> TranslateResult:
    """
    Translate text using Indic-Translate, preserving structure and handling limits.
    """
    source_lang = (args.source_lang or "en").lower().strip()
    target_lang = args.target_lang.lower().strip()

    # Validate languages
    if target_lang not in SUPPORTED_LANGUAGES:
        # Check if full name provided
        name_to_code = {v.lower(): k for k, v in SUPPORTED_LANGUAGES.items()}
        if target_lang in name_to_code:
            target_lang = name_to_code[target_lang]
        else:
            raise BodhanAPIError(
                f"Unsupported target language '{args.target_lang}'. Bodhan Indic-Translate only supports Indian languages: {', '.join(SUPPORTED_LANGUAGES.values())}.",
                status_code=400,
                details={"unsupported_language": args.target_lang}
            )

    if source_lang not in SUPPORTED_LANGUAGES:
        name_to_code = {v.lower(): k for k, v in SUPPORTED_LANGUAGES.items()}
        if source_lang in name_to_code:
            source_lang = name_to_code[source_lang]
        else:
            raise BodhanAPIError(
                f"Unsupported source language '{args.source_lang}'. Bodhan Indic-Translate only supports Indian languages and English.",
                status_code=400,
                details={"unsupported_language": args.source_lang}
            )

    if source_lang == target_lang:
        # No-op translation
        return TranslateResult(
            translated_text=args.text,
            source_lang=source_lang,
            target_lang=target_lang,
            token_count=len(args.text) // 4,
            metadata=ToolCallMetadata(cost_inr=0.0, latency_ms=1.0, model_name="indic-translate"),
        )

    chunks = split_text_into_chunks(args.text, max_chunk_chars=2500)
    translated_chunks = []
    total_tokens = 0
    total_latency = 0.0

    for chunk in chunks:
        res = client.translate(chunk, source_lang=source_lang, target_lang=target_lang)
        translated_chunks.append(res["translated_text"])
        total_tokens += res["token_count"]
        total_latency += res["latency_ms"]

    full_translated_text = "\n\n".join(translated_chunks)
    total_cost = calculate_translate_cost(total_tokens)

    return TranslateResult(
        translated_text=full_translated_text,
        source_lang=source_lang,
        target_lang=target_lang,
        token_count=total_tokens,
        metadata=ToolCallMetadata(
            cost_inr=total_cost,
            latency_ms=round(total_latency, 2),
            model_name="indic-translate",
        ),
    )
