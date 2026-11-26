"""The whole flow: paragraphs -> translate -> check -> sentences -> speak -> join.

This file knows nothing about Streamlit, so it can be tested without a page and
reused later (command line, API, browser extension).
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from . import config
from .audio_utils import join_wavs
from .errors import BodhanAPIError, InputError, TruncatedTranslationError
from .text_utils import (
    chunk_sentence,
    clean_for_speech,
    split_paragraphs,
    split_sentences,
    to_native_digits,
)


@dataclass
class NarrationResult:
    original_text: str
    target_language: str
    voice: str
    style: str | None
    paragraphs: list
    translated_paragraphs: list
    chunks: list
    wav_bytes: bytes = b""
    warnings: list = field(default_factory=list)


def check_input(text, language, voice):
    """Raise InputError with a friendly message if the request is not usable."""
    if not text or not text.strip():
        raise InputError("Enter some text first.")
    if len(text) > config.MAX_INPUT_CHARS:
        raise InputError(
            f"The text is {len(text)} characters. The limit is {config.MAX_INPUT_CHARS}."
        )
    if language not in config.LANGUAGES:
        raise InputError(f"Language '{language}' is not supported.")
    if voice not in config.VOICES[language]:
        raise InputError(f"Voice '{voice}' is not available for {config.LANGUAGES[language]}.")


def translate_paragraphs(text, language, client, progress=None):
    """Translate each paragraph. Stop if Bodhan says a translation was cut off.

    Returns (paragraphs, translated_paragraphs).
    """
    paragraphs = split_paragraphs(text)
    translated = []
    for number, paragraph in enumerate(paragraphs, start=1):
        if progress:
            progress("translate", number - 1, len(paragraphs))
        result, truncated = client.translate(paragraph, language)
        if truncated:
            raise TruncatedTranslationError(number)
        if not result.strip():
            raise BodhanAPIError(f"Bodhan returned an empty translation for paragraph {number}.")
        translated.append(result)
    if progress:
        progress("translate", len(paragraphs), len(paragraphs))
    return paragraphs, translated



def plan_chunks(translated_paragraphs):
    """Cut translations into speakable chunks, and decide the pause before each one.

    Returns (chunks, pauses): pauses has one entry per gap between chunks.
    """
    chunks = []
    pauses = []
    for p_index, paragraph in enumerate(translated_paragraphs):
        paragraph = clean_for_speech(paragraph)
        if not paragraph.strip():
            continue

        first_in_paragraph = True
        for sentence in split_sentences(paragraph):
            for piece in chunk_sentence(sentence, config.CHUNK_LIMIT):
                if chunks:
                    if first_in_paragraph and p_index > 0:
                        pauses.append(config.PARAGRAPH_PAUSE_MS)
                    else:
                        pauses.append(config.SENTENCE_PAUSE_MS)
                chunks.append(piece)
                first_in_paragraph = False
    return chunks, pauses



def speak_chunks(translated_paragraphs, language, voice, style, client, progress=None):
    """Turn translated paragraphs into one WAV. Returns (chunks, wav_bytes).

    Several chunks are sent at once, but the clips are joined in the original order.
    Progress is reported from this thread only, because Streamlit needs that.
    """
    chunks, pauses = plan_chunks(translated_paragraphs)
    if not chunks:
        raise InputError("There was nothing to read aloud.")
    digits = config.NATIVE_DIGITS.get(language)
    clips = [None] * len(chunks)
    done = 0
    if progress:
        progress("speak", 0, len(chunks))
    with ThreadPoolExecutor(max_workers=config.SPEAK_WORKERS) as pool:
        futures = {
            pool.submit(client.speak, to_native_digits(chunk, digits), voice, language, style): index
            for index, chunk in enumerate(chunks)
        }
        try:
            for future in as_completed(futures):
                clips[futures[future]] = future.result()
                done += 1
                if progress:
                    progress("speak", done, len(chunks))
        except Exception:
            for future in futures:
                future.cancel()
            raise
    return chunks, join_wavs(clips, pause_ms=pauses)


def narrate(text, language, voice, style, client, progress=None, on_translated=None):
    """Run everything.

    progress(stage, done, total) is called as work moves on.
    on_translated(translated_paragraphs) is called as soon as the translation is
    ready, before any speech, so the page can show it even if speech fails.
    """
    check_input(text, language, voice)
    paragraphs, translated = translate_paragraphs(text, language, client, progress)
    if on_translated:
        on_translated(translated)
    chunks, wav_bytes = speak_chunks(translated, language, voice, style, client, progress)
    return NarrationResult(
        original_text=text,
        target_language=language,
        voice=voice,
        style=style,
        paragraphs=paragraphs,
        translated_paragraphs=translated,
        chunks=chunks,
        wav_bytes=wav_bytes,
    )