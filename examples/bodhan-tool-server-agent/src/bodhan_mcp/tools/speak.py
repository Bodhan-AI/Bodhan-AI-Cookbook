"""Indic-Speak Tool for Bodhan MCP.

Synthesizes speech audio from Indian language text using Bodhan Indic-Speak.
Handles sentence/short-input chunking, audio concatenation, and AI-generated labeling.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from bodhan_mcp.cache import cache
from bodhan_mcp.clients.bodhan_client import bodhan_client
from bodhan_mcp.config import config, is_supported_language, normalize_language_code
from bodhan_mcp.media_utils import concat_wav_chunks, validate_file_reference

logger = logging.getLogger("bodhan.tools.speak")

# Sentence delimiters including Devanagari purna viram (।)
SENTENCE_SPLIT_REGEX = re.compile(r"([।\.\?!;\n]+)")


def split_text_for_speech(text: str, max_chars: int = 400) -> List[str]:
    """Split text into natural sentence fragments suitable for Indic-Speak pacing."""
    if len(text) <= max_chars:
        return [text.strip()]

    tokens = SENTENCE_SPLIT_REGEX.split(text)
    sentences = []
    current_sentence = ""

    for i in range(0, len(tokens) - 1, 2):
        chunk = tokens[i] + tokens[i + 1]
        if len(current_sentence) + len(chunk) > max_chars and current_sentence:
            sentences.append(current_sentence.strip())
            current_sentence = chunk
        else:
            current_sentence += chunk

    if len(tokens) % 2 == 1:
        current_sentence += tokens[-1]

    if current_sentence.strip():
        sentences.append(current_sentence.strip())

    return [s for s in sentences if s.strip()]


async def synthesize_speech(
    text: str = "",
    text_path: Optional[Union[str, Path]] = None,
    language: str = "hi",
    voice: str = "default",
    output_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Synthesize speech audio from text using Bodhan Indic-Speak.
    
    Args:
        text: Input text string in an Indian language or English.
        text_path: Path to text file containing the input text.
        language: Language code or name (e.g., 'hi', 'kn', 'ta').
        voice: Voice persona or accent identifier.
        output_path: Destination path for the generated audio file (.wav).
        
    Returns:
        Structured dictionary with audio file path, character count, cost, and AI disclaimer.
    """
    if text_path:
        p = validate_file_reference(text_path, must_exist=True)
        with open(p, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = text or ""

    if not content.strip():
        raise ValueError("Cannot synthesize speech from empty text.")

    norm_lang = normalize_language_code(language)
    if not norm_lang:
        raise ValueError(
            f"Unsupported language '{language}' for Indic-Speak. "
            f"Bodhan only supports the 22 scheduled Indian languages and English."
        )

    char_count = len(content)

    # Determine destination path
    if output_path:
        out_file = Path(output_path).resolve()
    else:
        out_dir = Path("artifacts/audio")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = (out_dir / f"speech_{norm_lang}_{abs(hash(content)) % 100000:05d}.wav").resolve()

    out_file.parent.mkdir(parents=True, exist_ok=True)

    # Check cache
    params = {"tool": "speak", "language": norm_lang, "voice": voice}
    cache_key = cache.generate_key("speak", params, content)
    cached_result = cache.get(cache_key)
    if cached_result and Path(cached_result.get("audio_path", "")).exists():
        cached_result["cached"] = True
        cached_result["cost_inr"] = 0.0
        return cached_result

    # Split into sentence chunks to preserve teacher-paced natural rhythm
    chunks = split_text_for_speech(content, max_chars=config.max_speak_chunk_chars)
    temp_chunk_files = []

    try:
        temp_dir = out_file.parent / f"_temp_{out_file.stem}"
        temp_dir.mkdir(parents=True, exist_ok=True)

        for idx, chunk in enumerate(chunks):
            audio_bytes = await bodhan_client.speak(
                text=chunk,
                language=norm_lang,
                voice=voice,
                output_format="wav",
            )
            part_path = temp_dir / f"part_{idx:03d}.wav"
            with open(part_path, "wb") as pf:
                pf.write(audio_bytes)
            temp_chunk_files.append(part_path)

        # Merge chunks if multiple
        if len(temp_chunk_files) == 1:
            temp_chunk_files[0].replace(out_file)
        else:
            concat_wav_chunks(temp_chunk_files, out_file)
    finally:
        # Cleanup temporary slice files
        for p in temp_chunk_files:
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass
        try:
            if temp_dir.exists():
                temp_dir.rmdir()
        except Exception:
            pass

    actual_cost = config.calculate_speak_cost(char_count)

    # Label generated audio with AI disclaimer metadata
    disclaimer_info = {
        "audio_file": str(out_file),
        "ai_generated": True,
        "model": "indic-speak",
        "voice": voice,
        "language": norm_lang,
        "character_count": char_count,
        "notice": "This audio was generated using Bodhan AI Indic-Speak and is AI-generated.",
    }

    # Write companion sidecar metadata file
    meta_path = out_file.with_suffix(".json")
    with open(meta_path, "w", encoding="utf-8") as mf:
        json.dump(disclaimer_info, mf, indent=2)

    result = {
        "audio_path": str(out_file),
        "metadata_path": str(meta_path),
        "character_count": char_count,
        "language": norm_lang,
        "voice": voice,
        "cost_inr": actual_cost,
        "ai_generated": True,
        "disclaimer": disclaimer_info["notice"],
        "cached": False,
    }

    cache.set(cache_key, result)
    return result
