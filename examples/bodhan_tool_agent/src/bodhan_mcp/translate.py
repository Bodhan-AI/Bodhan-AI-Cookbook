import os
import re
import logging

import requests
from dotenv import load_dotenv

load_dotenv()

BODHAN_BASE_URL = os.getenv(
    "BODHAN_BASE_URL",
    "https://api.bodhan.ai"
)

TRANSLATE_URL = f"{BODHAN_BASE_URL}/v1/chat/completions"

# Conservative chunk size for long text
MAX_CHUNK_CHARACTERS = 3000


def _split_text(text: str) -> list:
    """Split long text into sentence-based chunks."""

    # Split text into paragraphs first
    paragraphs = [
        paragraph.strip()
        for paragraph in text.splitlines()
        if paragraph.strip()
    ]

    sentences = []

    for paragraph in paragraphs:
        # Split on common sentence-ending punctuation
        parts = re.split(
            r"(?<=[.!?।])\s+",
            paragraph
        )

        sentences.extend(
            part.strip()
            for part in parts
            if part.strip()
        )

    chunks = []
    current_chunk = ""

    for sentence in sentences:

        # Handle extremely long sentences by splitting by words
        if len(sentence) > MAX_CHUNK_CHARACTERS:

            if current_chunk:
                chunks.append(current_chunk)
                current_chunk = ""

            words = sentence.split()
            word_chunk = ""

            for word in words:

                if (
                    len(word_chunk) + len(word) + 1
                    > MAX_CHUNK_CHARACTERS
                ):
                    chunks.append(word_chunk)
                    word_chunk = word
                else:
                    word_chunk = (
                        f"{word_chunk} {word}".strip()
                    )

            if word_chunk:
                chunks.append(word_chunk)

            continue

        # Add sentence to the current chunk
        if (
            len(current_chunk) + len(sentence) + 1
            <= MAX_CHUNK_CHARACTERS
        ):
            current_chunk = (
                f"{current_chunk} {sentence}".strip()
            )
        else:
            if current_chunk:
                chunks.append(current_chunk)

            current_chunk = sentence

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def _translate_chunk(
    text: str,
    target_language: str,
    api_key: str,
) -> dict:
    """Translate one text chunk using Bodhan."""

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "indic-translate",
        "messages": [
            {
                "role": "user",
                "content": text,
            }
        ],
        "target_language_code": target_language,
    }

    response = requests.post(
        TRANSLATE_URL,
        headers=headers,
        json=payload,
        timeout=60,
    )

    if response.status_code == 401:
        raise RuntimeError(
            "Bodhan authentication failed. "
            "Check your translation API key."
        )

    if response.status_code == 429:
        raise RuntimeError(
            "Bodhan rate limit reached. Try again later."
        )

    response.raise_for_status()

    result = response.json()

    try:
        translation = (
            result["choices"][0]["message"]["content"]
        )
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(
            "Unexpected Bodhan response: "
            "translation is missing."
        )

    if not translation:
        raise RuntimeError(
            "Bodhan returned an empty translation."
        )

    return {
        "translation": translation,
        "usage": result.get("usage"),
    }


def _combine_usage(usages: list) -> dict:
    """Combine numeric usage fields from multiple requests."""

    combined = {}

    for usage in usages:

        if not isinstance(usage, dict):
            continue

        for key, value in usage.items():

            if isinstance(value, (int, float)):
                combined[key] = (
                    combined.get(key, 0) + value
                )

    return combined if combined else None


def translate_text(
    text: str,
    target_language: str,
    script: str = "native",
) -> dict:
    """Translate text using Bodhan Indic-Translate."""

    api_key = os.getenv("BODHAN_TRANSLATE_KEY")

    if not api_key:
        raise ValueError(
            "BODHAN_TRANSLATE_KEY is not configured."
        )

    if not text or not text.strip():
        raise ValueError(
            "Input text cannot be empty."
        )

    if not target_language or not target_language.strip():
        raise ValueError(
            "Target language is required."
        )

    language_codes = {
        "english": "en",
        "bengali": "bn",
        "bangla": "bn",
        "hindi": "hi",
        "tamil": "ta",
        "telugu": "te",
        "marathi": "mr",
        "gujarati": "gu",
        "kannada": "kn",
        "malayalam": "ml",
        "punjabi": "pa",
        "odia": "or",
        "assamese": "as",
    }

    target_language = language_codes.get(
        target_language.strip().lower(),
        target_language.strip().lower()
    )

    allowed_scripts = {
        "native",
        "roman",
        "codemix",
    }

    if script not in allowed_scripts:
        raise ValueError(
            f"Invalid script '{script}'. "
            "Choose native, roman or codemix."
        )

    # Native script is currently verified
    if script != "native":
        raise ValueError(
            "Only native script has been verified "
            "with the current Bodhan "
            "chat-completions endpoint."
        )

    # Split long input automatically
    chunks = _split_text(text)

    if not chunks:
        raise ValueError(
            "Unable to create translation chunks."
        )

    translated_chunks = []
    usages = []

   # Translate each chunk separately
    for chunk_number, chunk in enumerate(chunks, start=1):

        logging.info(
            "Translating chunk %s/%s...",
            chunk_number,
            len(chunks),
        )

        result = _translate_chunk(
            text=chunk,
            target_language=target_language,
            api_key=api_key,
        )

        translated_chunks.append(
            result["translation"]
        )

    usages.append(result.get("usage"))

    # Combine translations in original order
    final_translation = "\n".join(
        translated_chunks
    )

    total_usage = _combine_usage(usages)

    return {
        "translation": final_translation,
        "target_language": target_language,
        "script": script,
        "usage": total_usage,
        "model": "indic-translate",
        "chunks_processed": len(chunks),
    }